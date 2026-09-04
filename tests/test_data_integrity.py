# -*- coding: utf-8 -*-
"""数据完整性校验模块单元测试(内存 SQLite)。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.models.valuation import CnBondYield, IndexDailyQuote
from backend.services.data_integrity import (
    get_entity_codes,
    get_table_summary,
    scan_all_daily_tables,
    scan_date_gaps_generic,
)


def _add_quotes(db, code: str, dates: list[date]):
    for d in dates:
        db.add(IndexDailyQuote(index_code=code, trade_date=d, close=100.0))
    db.commit()


class TestTableSummary:
    def test_empty_returns_none(self, db):
        assert get_table_summary(db, IndexDailyQuote, "trade_date", "index_code", "399376") is None

    def test_summary_fields(self, db):
        _add_quotes(db, "399376", [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)])
        s = get_table_summary(db, IndexDailyQuote, "trade_date", "index_code", "399376")
        assert s == {
            "count": 3,
            "first": date(2024, 1, 2),
            "last": date(2024, 1, 4),
        }

    def test_entity_isolation(self, db):
        _add_quotes(db, "A", [date(2024, 1, 2)])
        _add_quotes(db, "B", [date(2024, 1, 3), date(2024, 1, 4)])
        assert get_table_summary(db, IndexDailyQuote, "trade_date", "index_code", "A")["count"] == 1
        assert get_table_summary(db, IndexDailyQuote, "trade_date", "index_code", "B")["count"] == 2
        assert get_entity_codes(db, IndexDailyQuote, "index_code") == ["A", "B"]


class TestGapScan:
    def test_no_gap_in_continuous_dates(self, db):
        dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(20)]
        _add_quotes(db, "X", dates)
        assert scan_date_gaps_generic(db, IndexDailyQuote, "trade_date", "index_code", "X") == []

    def test_gap_detected(self, db):
        """中间断 20 天 → 报 1 处空洞。"""
        dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(10)]
        dates += [date(2024, 2, 1) + timedelta(days=i) for i in range(10)]
        _add_quotes(db, "X", dates)
        gaps = scan_date_gaps_generic(db, IndexDailyQuote, "trade_date", "index_code", "X")
        assert len(gaps) == 1
        assert gaps[0]["prev"] == "2024-01-10"
        assert gaps[0]["next"] == "2024-02-01"
        assert gaps[0]["gap_days"] == 22

    def test_normal_weekend_not_flagged(self, db):
        """周五 -> 周一隔 3 天不算空洞。"""
        _add_quotes(db, "X", [date(2024, 1, 5), date(2024, 1, 8)])
        assert scan_date_gaps_generic(db, IndexDailyQuote, "trade_date", "index_code", "X") == []

    def test_chinese_new_year_not_flagged_with_default(self, db):
        """春节长假约 9 天, 默认阈值 11 不误报。"""
        _add_quotes(db, "X", [date(2024, 2, 8), date(2024, 2, 18)])
        assert scan_date_gaps_generic(db, IndexDailyQuote, "trade_date", "index_code", "X") == []

    def test_global_mode_scans_whole_table(self, db):
        """global 模式合并所有实体日期后扫描(用于转债类表)。"""
        _add_quotes(db, "A", [date(2024, 1, 1)])
        _add_quotes(db, "B", [date(2024, 1, 1), date(2024, 3, 1)])
        gaps = scan_date_gaps_generic(db, IndexDailyQuote, "trade_date")
        assert len(gaps) == 1  # 2024-01-01 -> 2024-03-01


class TestScanAllTables:
    def test_report_structure(self, db):
        dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(15)]
        _add_quotes(db, "399373", dates)
        _add_quotes(db, "399376", dates)
        db.add(CnBondYield(trade_date=date(2024, 1, 1), yield_10y=2.5))
        db.commit()

        report = scan_all_daily_tables(db)
        names = [t["name"] for t in report["tables"]]
        assert len(names) == 7
        assert report["total_gaps"] == 0

        quote_table = next(t for t in report["tables"] if t["name"] == "指数日线(风格轮动)")
        assert {e["code"] for e in quote_table["entities"]} == {"399373", "399376"}

    def test_gap_across_tables_reported(self, db):
        """指数日线有洞、其他表正常 → 只在对应表报告。"""
        d1 = [date(2024, 1, 1) + timedelta(days=i) for i in range(5)]
        d2 = d1 + [date(2024, 3, 1)]  # 指数日线多一个远期点 → 空洞
        _add_quotes(db, "399373", d2)
        for d in d1:
            db.add(CnBondYield(trade_date=d))
        db.commit()

        report = scan_all_daily_tables(db)
        assert report["total_gaps"] == 1
        broken = next(t for t in report["tables"] if t["gaps"])
        assert broken["name"] == "指数日线(风格轮动)"
        assert broken["gaps"][0]["entity"] == "399373"
