# -*- coding: utf-8 -*-
"""research_store 配对发布/修订/日历/加载测试(内存库, 不触 akshare)。"""
from datetime import date, datetime, timezone

import pytest

from backend.models.research import (
    ResearchDataRevision,
    ResearchDataSnapshot,
)
from backend.services import research_store
from backend.services.market_data import DailyBar, TradeSession


def _utcnow():
    return datetime(2026, 9, 18, 8, 0, 0)


def _bars(dates: list[date], closes: list[float] | None = None) -> list[DailyBar]:
    closes = closes or [10.0] * len(dates)
    return [
        DailyBar(
            trade_date=d, open=c - 0.2, high=c + 0.3, low=c - 0.3, close=c,
            volume=100.0, amount=1000.0, volume_unit="share", source="test",
        )
        for d, c in zip(dates, closes)
    ]


_DATES = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]


def _publish(db, raw_bars, hfq_bars, *, source="akshare-eastmoney"):
    return research_store.publish_paired_snapshot(
        db, "600900.SH", raw_bars, hfq_bars,
        source=source, request_start=_DATES[0], request_end=_DATES[-1], now=_utcnow(),
    )


def test_publish_rejects_single_side_with_zero_bar_writes(db):
    result = _publish(db, _bars(_DATES), [])
    assert result.status == "rejected"
    assert "单侧为空" in result.error
    # 快照留痕(REJECTED), 但零 bar 写入
    snapshots = db.query(ResearchDataSnapshot).all()
    assert len(snapshots) == 1 and snapshots[0].status == "REJECTED"
    assert research_store.load_paired_bars(db, "600900.SH", end_date=_DATES[-1]) == []


def test_publish_rejects_date_misalignment(db):
    hfq_dates = _DATES[:2]  # 缺最后一天
    result = _publish(db, _bars(_DATES), _bars(hfq_dates))
    assert result.status == "rejected"
    assert "日期错位" in result.error
    assert research_store.load_paired_bars(db, "600900.SH", end_date=_DATES[-1]) == []


def test_publish_usable_writes_bars_and_snapshot(db):
    result = _publish(db, _bars(_DATES), _bars(_DATES, [20.0, 20.1, 20.2]))
    assert result.status == "success"
    assert result.inserted_rows == 6  # raw 3 + hfq 3
    snapshot = research_store.latest_usable_snapshot(db, "600900.SH")
    assert snapshot is not None and snapshot.status == "USABLE"
    assert snapshot.dates_match is True
    assert snapshot.last_date == _DATES[-1]
    paired = research_store.load_paired_bars(db, "600900.SH", end_date=_DATES[-1])
    assert len(paired) == 3
    assert paired[0]["raw_close"] == 10.0 and paired[0]["hfq_close"] == 20.0


def test_publish_same_content_rerun_is_unchanged_and_new_snapshot(db):
    _publish(db, _bars(_DATES), _bars(_DATES, [20.0, 20.1, 20.2]))
    later = _utcnow().replace(hour=9)
    result = research_store.publish_paired_snapshot(
        db, "600900.SH", _bars(_DATES), _bars(_DATES, [20.0, 20.1, 20.2]),
        source="akshare-eastmoney", request_start=_DATES[0], request_end=_DATES[-1],
        now=later,
    )
    assert result.status == "success"
    assert result.inserted_rows == 0 and result.revised_rows == 0
    assert result.unchanged_rows == 6
    assert len(db.query(ResearchDataSnapshot).all()) == 2  # append-only


def test_publish_hash_change_writes_revision_with_full_payloads(db):
    _publish(db, _bars(_DATES), _bars(_DATES, [20.0, 20.1, 20.2]))
    # 供应商修订: 中间一日 hfq close 20.1 → 21.1
    result = research_store.publish_paired_snapshot(
        db, "600900.SH", _bars(_DATES), _bars(_DATES, [20.0, 21.1, 20.2]),
        source="akshare-eastmoney", request_start=_DATES[0], request_end=_DATES[-1],
        now=_utcnow().replace(hour=10),
    )
    assert result.status == "success"
    assert result.revised_rows == 1
    revisions = db.query(ResearchDataRevision).all()
    assert len(revisions) == 1
    revision = revisions[0]
    assert revision.table_name == "research_daily_bar_adjusted"
    assert revision.business_key == "600900.SH|2026-01-06|HFQ"
    assert '"close": 20.1' in revision.old_payload
    assert '"close": 21.1' in revision.new_payload  # 完整载荷而非仅哈希
    paired = research_store.load_paired_bars(db, "600900.SH", end_date=_DATES[-1])
    assert paired[1]["hfq_close"] == 21.1  # 现值已更新


def test_upsert_trade_calendar_versioned(db):
    sessions = [TradeSession(trade_date=date(2026, 1, 5), is_open=True),
                TradeSession(trade_date=date(2026, 1, 6), is_open=True)]
    added = research_store.upsert_trade_calendar(db, sessions, source="akshare-eastmoney", now=_utcnow())
    assert added == 2
    # 同内容重跑: 不新增; 变化: 更新并换版本
    added_again = research_store.upsert_trade_calendar(
        db, sessions, source="akshare-eastmoney", now=_utcnow().replace(hour=11),
    )
    assert added_again == 0
    changed = [TradeSession(trade_date=date(2026, 1, 5), is_open=False)]
    research_store.upsert_trade_calendar(db, changed, source="akshare-eastmoney", now=_utcnow().replace(hour=12))
    calendar = dict(research_store.load_calendar(db, date(2026, 1, 1), date(2026, 1, 31)))
    assert calendar == {date(2026, 1, 5): False, date(2026, 1, 6): True}


def test_upsert_securities_adds_and_updates(db):
    targets = [
        {"symbol": "600900.SH", "name": "长江电力", "type": "stock", "selection_list": "高股息"},
        {"symbol": "510300.SH", "name": "沪深300ETF", "type": "etf"},
    ]
    added = research_store.upsert_securities(db, targets)
    assert added == 2
    # 更新不重复插入
    research_store.upsert_securities(db, targets)
    rows = db.query(__import__("backend.models.research", fromlist=["ResearchSecurity"]).ResearchSecurity).all()
    assert len(rows) == 2
    by_symbol = {row.symbol: row for row in rows}
    assert by_symbol["600900.SH"].security_type == "STOCK"
    assert by_symbol["600900.SH"].exchange == "SSE"
    assert by_symbol["510300.SH"].security_type == "ETF"
    assert by_symbol["510300.SH"].selection_list == "手动ETF"


def test_upsert_securities_rejects_bad_symbol(db):
    with pytest.raises(ValueError):
        research_store.upsert_securities(db, [{"symbol": "600900", "name": "x", "type": "stock"}])


def test_load_paired_bars_skips_unpaired_dates(db):
    _publish(db, _bars(_DATES), _bars(_DATES, [20.0, 20.1, 20.2]))
    # 删除一侧的中间日, 制造配对缺口
    from backend.models.research import ResearchDailyBarAdjusted
    row = db.query(ResearchDailyBarAdjusted).filter_by(trade_date=_DATES[1]).one()
    db.delete(row)
    db.commit()
    paired = research_store.load_paired_bars(db, "600900.SH", end_date=_DATES[-1])
    assert [p["trade_date"] for p in paired] == [_DATES[0], _DATES[2]]
    health = research_store.data_health(db)
    # 无名单 → 空报告; 有名单时逐 symbol 展示
    assert isinstance(health, list)


# ---------------------------------------------------------------------------
# 权益事件日历(腾讯换源后的主检测来源)
# ---------------------------------------------------------------------------


def _events() -> list:
    from backend.services.market_data import CorporateEvent

    return [
        CorporateEvent(event_date=date(2026, 7, 17), factor=1.7166614532470703),
        CorporateEvent(event_date=date(2026, 9, 18), factor=1.0, cumulative_dividend=5.3905),
    ]


def test_upsert_and_load_corporate_events(db):
    added = research_store.upsert_corporate_events(
        db, "600900.SH", _events(), source="tencent-fqkline", now=_utcnow(),
    )
    assert added == 2
    assert research_store.load_corporate_event_dates(db, "600900.SH") == {
        date(2026, 7, 17), date(2026, 9, 18),
    }
    # 其他标的隔离
    assert research_store.load_corporate_event_dates(db, "511010.SH") == set()


def test_upsert_corporate_events_updates_factor_keeps_dates(db):
    from backend.services.market_data import CorporateEvent

    research_store.upsert_corporate_events(
        db, "600900.SH", _events(), source="tencent-fqkline", now=_utcnow(),
    )
    # 供应商修订因子: 事件日期不变, 因子/抓取时间覆盖更新, 不新增行
    added = research_store.upsert_corporate_events(
        db, "600900.SH",
        [CorporateEvent(event_date=date(2026, 7, 17), factor=1.72)],
        source="tencent-fqkline", now=_utcnow().replace(hour=9),
    )
    assert added == 0
    dates = research_store.load_corporate_event_dates(db, "600900.SH")
    assert dates == {date(2026, 7, 17), date(2026, 9, 18)}


def test_upsert_corporate_events_isolated_by_source(db):
    from backend.services.market_data import CorporateEvent

    research_store.upsert_corporate_events(
        db, "600900.SH", _events(), source="tencent-fqkline", now=_utcnow(),
    )
    # 不同 source 的同日期事件各行独立(唯一键含 source)
    research_store.upsert_corporate_events(
        db, "600900.SH",
        [CorporateEvent(event_date=date(2026, 7, 17))],
        source="akshare-eastmoney", now=_utcnow(),
    )
    assert research_store.load_corporate_event_dates(db, "600900.SH") == {date(2026, 7, 17), date(2026, 9, 18)}
