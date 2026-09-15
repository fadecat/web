from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.models.commodity import CommodityDailyPrice, CommodityInstrument
from backend.models.database import Base
from backend.services.commodity_source import CommodityPriceRecord
from backend.services.commodity_store import CommodityStore


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(CommodityInstrument(code="RB0", name="螺纹钢", market="domestic", category="黑色建材", source="akshare", enabled=True, display_order=1))
    session.commit()
    return session


def _rows(*closes):
    return [CommodityPriceRecord(date(2026, 1, i + 1), float(close), source="test") for i, close in enumerate(closes)]


def test_store_is_idempotent_and_revises_changed_history_without_commit():
    db = _session()
    store = CommodityStore(db)
    first = store.upsert_prices("RB0", _rows(1, 2, 3), source_latest_date=date(2026, 1, 3))
    db.commit()
    second = store.upsert_prices("RB0", _rows(1, 2, 3), source_latest_date=date(2026, 1, 3))
    assert first.inserted_rows == 3 and second.inserted_rows == 0 and second.revised_rows == 0
    db.commit()
    revised = store.upsert_prices("RB0", _rows(1, 20, 3), source_latest_date=date(2026, 1, 3))
    assert revised.revised_rows == 1
    db.commit()
    assert db.scalar(select(CommodityDailyPrice).where(CommodityDailyPrice.trade_date == date(2026, 1, 2))).close == 20


def test_store_rejects_source_regression_or_short_result_without_overwriting():
    db = _session()
    store = CommodityStore(db)
    store.upsert_prices("RB0", _rows(1, 2, 3, 4, 5), source_latest_date=date(2026, 1, 5))
    db.commit()
    regressed = store.upsert_prices("RB0", _rows(9, 9), source_latest_date=date(2026, 1, 4))
    assert regressed.status == "suspicious" and regressed.inserted_rows == 0
    short = store.upsert_prices("RB0", _rows(8, 8, 8), source_latest_date=date(2026, 1, 5))
    assert short.status == "suspicious" and short.revised_rows == 0
    db.commit()
    assert db.scalar(select(CommodityDailyPrice).where(CommodityDailyPrice.trade_date == date(2026, 1, 1))).close == 1


def test_store_failure_state_increments_and_success_clears_error():
    db = _session()
    store = CommodityStore(db)
    failed = store.mark_failed("RB0", "bad source")
    assert failed.status == "failed" and failed.consecutive_failures == 1
    db.commit()
    succeeded = store.mark_success("RB0", source_latest_date=date(2026, 1, 1), status="unchanged")
    assert succeeded.status == "unchanged" and succeeded.consecutive_failures == 0 and succeeded.last_error is None
