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
    assert [row["code"] for row in rows] == ["B", "A"]
    row_a = next(row for row in rows if row["code"] == "A")
    assert row_a["d21"]["percentile"] == 95
    assert row_a["d63"] == {"percentile": None, "sample_count": None, "signal": None}
    assert row_a["current_status"] == "divergent"
    assert row_a["sync_status"] == "success"
    assert row_a["last_attempt_at"] == "2026-09-16T00:00:00+08:00"
    assert row_a["last_success_at"] == "2026-09-16T00:00:00+08:00"
    assert row_a["last_error"] is None
    assert row_a["windows"]["d21"]["signal"] == "high"
    assert row_a["windows"]["y10"]["percentile"] is None
    assert row_a["signal"] == "divergent"
    assert row_a["status_label"] == "周期分化"
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


def test_overview_date_uses_failed_and_stale_prices(db):
    db.add_all([
        CommodityInstrument(code="FAILED", name="失败", market="m", category="x", source="test", enabled=True, display_order=3),
        CommodityInstrument(code="STALE", name="过期", market="m", category="x", source="test", enabled=True, display_order=4),
    ])
    db.add_all([
        CommodityDailyPrice(instrument_code="FAILED", trade_date=date(2026, 9, 16), close=1, source="test"),
        CommodityDailyPrice(instrument_code="STALE", trade_date=date(2026, 9, 15), close=2, source="test"),
        CommoditySyncState(instrument_code="FAILED", status="failed", last_error="down"),
        CommoditySyncState(instrument_code="STALE", status="success"),
    ])
    db.commit()
    from backend.services.commodity_queries import CommodityQueryService

    overview = CommodityQueryService(db, now=datetime(2026, 9, 16, 16)).overview()
    assert overview["data_date"] == "2026-09-16"


def test_signal_filter_keeps_overall_failed_stale_independent_of_window(db):
    _seed(db)
    db.add(CommoditySyncState(instrument_code="B", status="success"))
    db.get(CommoditySyncState, "A").status = "failed"
    db.get(CommoditySyncState, "A").last_error = "down"
    db.commit()
    from backend.services.commodity_queries import CommodityQueryService

    service = CommodityQueryService(db, now=datetime(2026, 9, 15, 16))
    assert [row["code"] for row in service.list_instruments(signal="failed", window="y5")] == ["A"]
    assert [row["code"] for row in service.list_instruments(signal="stale", window="y5")] == ["B"]
    assert [row["code"] for row in service.list_instruments(signal="high", window="d21")] == ["A"]


def test_sync_stale_overrides_persisted_signal(db):
    _seed(db)
    db.get(CommoditySyncState, "A").status = "stale"
    db.commit()
    from backend.services.commodity_queries import CommodityQueryService

    row = next(row for row in CommodityQueryService(db, now=datetime(2026, 9, 15, 16)).list_instruments() if row["code"] == "A")
    assert row["current_status"] == "stale"


def test_divergent_filter_uses_overall_status_even_with_window(db):
    _seed(db)
    from backend.services.commodity_queries import CommodityQueryService

    service = CommodityQueryService(db, now=datetime(2026, 9, 15, 16))
    assert [row["code"] for row in service.list_instruments(signal="divergent", window="d21")] == ["A"]


def test_signal_sort_has_explicit_order_and_nulls_stay_last(db):
    from backend.services.commodity_queries import CommodityQueryService

    rows = []
    order = ["failed", "stale", "divergent", "high", "low", "neutral", "insufficient"]
    for display_order, signal in enumerate(order, start=1):
        rows.append({"code": signal, "display_order": display_order, "signal": signal, "latest_price": display_order, "data_date": None, "windows": {code: {"percentile": None, "signal": None, "sample_count": None} for code in ("d21", "d63", "y1", "y3", "y5", "y10")}})
    rows.extend([
        {"code": "value", "display_order": 9, "signal": "neutral", "latest_price": 10, "data_date": None, "windows": {code: {"percentile": 42 if code == "y5" else None, "signal": None, "sample_count": None} for code in ("d21", "d63", "y1", "y3", "y5", "y10")}},
        {"code": "value_tie", "display_order": 0, "signal": "neutral", "latest_price": 10, "data_date": None, "windows": {code: {"percentile": 42 if code == "y5" else None, "signal": None, "sample_count": None} for code in ("d21", "d63", "y1", "y3", "y5", "y10")}},
        {"code": "value_low", "display_order": 10, "signal": "neutral", "latest_price": 10, "data_date": None, "windows": {code: {"percentile": 7 if code == "y5" else None, "signal": None, "sample_count": None} for code in ("d21", "d63", "y1", "y3", "y5", "y10")}},
        {"code": "null", "display_order": 8, "signal": "neutral", "latest_price": None, "data_date": None, "windows": {code: {"percentile": None, "signal": None, "sample_count": None} for code in ("d21", "d63", "y1", "y3", "y5", "y10")}},
    ])
    service = CommodityQueryService(db)
    service._all_rows = lambda: rows[:7]
    assert [row["code"] for row in service.list_instruments()] == order
    assert [row["code"] for row in service.list_instruments(sort_order="asc")] == list(reversed(order))
    service._all_rows = lambda: rows
    asc = [row["code"] for row in service.list_instruments(sort_by="y5", sort_order="asc")]
    desc = [row["code"] for row in service.list_instruments(sort_by="y5", sort_order="desc")]
    assert asc[:3] == ["value_low", "value_tie", "value"] and asc[-1] == "null"
    assert desc[:2] == ["value_tie", "value"] and desc[-1] == "null"


def test_current_queries_only_use_latest_v1_percentiles_and_leap_day_range(db):
    db.add(CommodityInstrument(code="LEAP", name="闰日", market="m", category="x", source="test", enabled=True, display_order=1))
    db.add_all([
        CommodityDailyPrice(instrument_code="LEAP", trade_date=date(2023, 2, 27), close=1, source="test"),
        CommodityDailyPrice(instrument_code="LEAP", trade_date=date(2023, 2, 28), close=2, source="test"),
        CommodityDailyPrice(instrument_code="LEAP", trade_date=date(2024, 2, 29), close=3, source="test"),
        CommodityPercentileDaily(instrument_code="LEAP", trade_date=date(2023, 2, 28), window_code="d21", window_days=21, percentile=11, sample_count=21, signal="low", algorithm_version="v1"),
        CommodityPercentileDaily(instrument_code="LEAP", trade_date=date(2024, 2, 29), window_code="d21", window_days=21, percentile=91, sample_count=21, signal="high", algorithm_version="old"),
        CommodityPercentileDaily(instrument_code="LEAP", trade_date=date(2024, 2, 29), window_code="d21", window_days=21, percentile=92, sample_count=21, signal="high", algorithm_version="v1"),
    ])
    db.commit()
    from backend.services.commodity_queries import CommodityQueryService

    service = CommodityQueryService(db, now=datetime(2024, 2, 29, 16))
    statements = []
    engine = db.get_bind()
    def listener(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "commodity_percentile_daily" in statement.lower():
            statements.append(statement.lower())
    event.listen(engine, "before_cursor_execute", listener)
    try:
        current = service.list_instruments()[0]
        assert current["windows"]["d21"]["percentile"] == 92
        assert service.get_detail("LEAP")["windows"]["d21"]["percentile"] == 92
        assert service.overview()["data_date"] == "2024-02-29"
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert len(statements) == 3
    assert all("commodity_daily_price" in statement and "algorithm_version" in statement for statement in statements)
    history = service.history("LEAP", "1y")
    assert [item["date"] for item in history["prices"]] == ["2023-02-28", "2024-02-29"]
    assert [item["percentile"] for item in history["signals"]] == [11, 92]
