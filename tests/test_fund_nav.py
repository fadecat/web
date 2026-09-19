# -*- coding: utf-8 -*-
"""场外基金净值链路单测(P1): 蛋卷响应解析 / 链式分红再投复权 / 幂等落库。

⚠ 这个文件补的是一个**测试缺口**: `services/fund_nav.py` 里的链式复权是 P1 里
最容易写错的一段(实测曾把"中间缺 percentage"当成链式起点, 一次缺失就掐断整条复权链,
会让 513100 这类有份额折算的标的收益从 485% 变成 17%), 但此前没有专门的纯函数测试。
本文件全部不触网(解析函数只吃 dict), 落库用内存 SQLite。
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base
from backend.models.research import FundNavDaily
from backend.services import fund_nav


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _history(items: list[dict]) -> dict:
    return {"data": {"total_items": len(items), "items": items}}


# ---------------------------------------------------------------------------
# 解析: 只认 data.items; value 陷阱; 成立首日无 percentage
# ---------------------------------------------------------------------------

class TestParseNavHistory:
    def test_extracts_facts_and_sorts_ascending(self) -> None:
        """蛋卷返回**降序**, 必须转成升序(链式复权依赖顺序)。"""
        rows = fund_nav.parse_nav_history(_history([
            {"date": "2026-01-06", "nav": 2.0, "percentage": 1.0},
            {"date": "2026-01-05", "nav": 1.9802, "percentage": 1.01},
            {"date": "2026-01-04", "nav": 1.9604, "percentage": None},
        ]))
        assert [r["nav_date"] for r in rows] == [
            date(2026, 1, 4), date(2026, 1, 5), date(2026, 1, 6),
        ]
        assert rows[-1]["unit_nav"] == 2.0
        assert rows[-1]["daily_return_pct"] == 1.0

    def test_first_day_without_percentage_is_legal(self) -> None:
        """成立首日**合法缺失** percentage → None, 不能报错也不能当 0。"""
        rows = fund_nav.parse_nav_history(_history([
            {"date": "2003-12-02", "nav": 1.0, "value": 1.0},
        ]))
        assert rows[0]["daily_return_pct"] is None

    def test_value_field_is_not_used_as_cumulative_nav(self) -> None:
        """⚠ `value` 恒等于 `nav`, **不是累计净值** —— 用它算收益会得到量级错误。

        这里显式给一个"看起来像累计净值"的 value, 断言解析结果只认 nav。
        """
        rows = fund_nav.parse_nav_history(_history([
            {"date": "2026-01-05", "nav": 1.98, "value": 9.99, "percentage": 1.0},
        ]))
        assert rows[0]["unit_nav"] == 1.98

    def test_percentage_accepts_string_and_rejects_garbage(self) -> None:
        rows = fund_nav.parse_nav_history(_history([
            {"date": "2026-01-05", "nav": 1.0, "percentage": "1.25"},
            {"date": "2026-01-06", "nav": 1.1, "percentage": "n/a"},
        ]))
        assert rows[0]["daily_return_pct"] == 1.25
        assert rows[1]["daily_return_pct"] is None

    @pytest.mark.parametrize("payload,fragment", [
        ({}, "缺少 data"),
        ({"data": {}}, "缺少 data.items"),
        ({"data": {"items": [{"date": "bad", "nav": 1.0}]}}, "invalid nav date"),
        ({"data": {"items": [{"date": "2026-01-05", "nav": "x"}]}}, "invalid nav value"),
        ({"data": {"items": ["nope"]}}, "invalid nav item"),
    ])
    def test_malformed_payload_raises(self, payload: dict, fragment: str) -> None:
        """结构不对必须报错, 不静默退回空列表(否则会被当成"这只基金没数据")。"""
        with pytest.raises(ValueError, match=fragment):
            fund_nav.parse_nav_history(payload)


# ---------------------------------------------------------------------------
# 链式分红再投复权
# ---------------------------------------------------------------------------

class TestChainAdjNav:
    def test_first_row_is_the_chain_origin(self) -> None:
        """首行(成立首日, 无 percentage)是链起点: adj = unit_nav, 不乘任何因子。"""
        rows = fund_nav.parse_nav_history(_history([
            {"date": "2026-01-05", "nav": 1.9604},                    # 成立首日
            {"date": "2026-01-06", "nav": 1.9802, "percentage": 1.01},
        ]))
        chained = fund_nav.chain_adj_nav(rows)
        assert chained[0].adj_nav == pytest.approx(1.9604)
        assert chained[1].adj_nav == pytest.approx(1.9604 * 1.0101)

    def test_missing_percentage_in_the_middle_does_not_reset_the_chain(self) -> None:
        """⭐ 回归: 中间行缺 percentage 时**不得**把链重置为 unit_nav。

        这条是实测踩出来的: 一次中间缺失若按"新起点"处理, 整条复权链就断在这里,
        净值口径从"分红再投"退化成"单位净值首末比"(量级差几十倍)。
        """
        rows = fund_nav.parse_nav_history(_history([
            {"date": "2026-01-01", "nav": 1.9604},                      # 成立首日
            {"date": "2026-01-05", "nav": 1.9802, "percentage": 1.01},
            {"date": "2026-01-06", "nav": 2.0, "percentage": None},     # 中间缺失
            {"date": "2026-01-07", "nav": 2.9703, "percentage": 1.0},
            {"date": "2026-01-08", "nav": 3.0, "percentage": 1.0},
        ]))
        chained = fund_nav.chain_adj_nav(rows)
        # 01-06 缺失按 0% 处理(链不重置), 于是 01-08 应是三次连乘
        assert chained[2].adj_nav == pytest.approx(1.9604 * 1.0101)
        assert chained[4].adj_nav == pytest.approx(1.9604 * 1.0101 * 1.01 * 1.01)
        # 且单调递增(链断了会出现台阶式跳变, 这里防御性断言不倒退)
        navs = [r.adj_nav for r in chained]
        assert all(b >= a for a, b in zip(navs, navs[1:]))

    def test_chain_equals_compounded_daily_returns(self) -> None:
        """链式结果必须等于「起点 × 逐日 (1+pct/100) 连乘」—— 这是口径的定义。"""
        items = [
            {"date": f"2026-01-{d:02d}", "nav": 1.0, "percentage": 1.0}
            for d in range(1, 6)
        ]
        chained = fund_nav.chain_adj_nav(fund_nav.parse_nav_history(_history(items)))
        assert chained[0].adj_nav == pytest.approx(1.0)
        assert chained[-1].adj_nav == pytest.approx(1.01 ** 4)  # 首行不乘因子

    def test_empty_input(self) -> None:
        assert fund_nav.chain_adj_nav([]) == []


# ---------------------------------------------------------------------------
# 详情: 基金经理 / 类型描述 / 成立日
# ---------------------------------------------------------------------------

class TestParseFundDetail:
    def test_extracts_name_type_found_date_and_manager(self) -> None:
        got = fund_nav.parse_fund_detail({"data": {
            "fd_name": "富国天利增长债券", "type_desc": "债券型-普通债券",
            "found_date": "2003-12-02", "manager_name": "黄纪亮",
        }})
        assert got == {
            "name": "富国天利增长债券",
            "type_desc": "债券型-普通债券",
            "found_date": "2003-12-02",
            "manager": "黄纪亮",
        }

    def test_manager_is_optional(self) -> None:
        got = fund_nav.parse_fund_detail({"data": {"fd_name": "某基金"}})
        assert got["manager"] is None and got["found_date"] is None

    def test_missing_data_raises(self) -> None:
        """连 `data` 都没有(场内 ETF 的典型响应) → 抛错, 由调用方降级。"""
        with pytest.raises(ValueError, match="缺少 data"):
            fund_nav.parse_fund_detail({})
        with pytest.raises(ValueError, match="缺少 data"):
            fund_nav.parse_fund_detail({"data": {}})

    def test_error_body_parses_to_all_none(self) -> None:
        """`{"data": {"error": ...}}` 非空 → 解析出全 None, **不在这里抛**。

        调用方(probe)按 `resolved = bool(name)` 降级即可; 把可降级的情形当故障
        会让「蛋卷详情暂不可用但净值可同步」的场内 ETF 直接报错。
        """
        got = fund_nav.parse_fund_detail({"data": {"error": "该基金暂不销售"}})
        assert got == {"name": None, "type_desc": None, "found_date": None, "manager": None}


# ---------------------------------------------------------------------------
# 幂等落库与统计
# ---------------------------------------------------------------------------

class TestUpsertFundNav:
    def test_writes_rows_and_is_idempotent(self, db) -> None:
        rows = fund_nav.chain_adj_nav(fund_nav.parse_nav_history(_history([
            {"date": "2026-01-05", "nav": 1.98, "percentage": 1.0},
            {"date": "2026-01-06", "nav": 2.0, "percentage": 1.01},
        ])))
        first = fund_nav.upsert_fund_nav(db, "100018.OF", rows)
        assert first["rows_written"] == 2 and first["total"] == 2

        # 重复同步同一批: 不新增行, 覆盖写(唯一键 symbol+nav_date 幂等)
        again = fund_nav.upsert_fund_nav(db, "100018.OF", rows)
        assert again["rows_written"] == 2
        assert db.scalar(select(func.count()).select_from(FundNavDaily)) == 2

    def test_revision_overwrites_facts(self, db) -> None:
        """净值会被基金公司修正 → 重同步必须**覆盖**旧事实与派生值。"""
        fund_nav.upsert_fund_nav(db, "100018.OF", fund_nav.chain_adj_nav(
            fund_nav.parse_nav_history(_history([{"date": "2026-01-05", "nav": 1.98, "percentage": 1.0}]))
        ))
        fund_nav.upsert_fund_nav(db, "100018.OF", fund_nav.chain_adj_nav(
            fund_nav.parse_nav_history(_history([{"date": "2026-01-05", "nav": 1.99, "percentage": 2.0}]))
        ))
        row = db.scalar(select(FundNavDaily).where(FundNavDaily.nav_date == date(2026, 1, 5)))
        assert row.unit_nav == pytest.approx(1.99)
        assert row.adj_nav == pytest.approx(1.99)

    def test_stats_reflect_range(self, db) -> None:
        assert fund_nav.fund_nav_stats(db, "100018.OF") == (0, None, None)
        fund_nav.upsert_fund_nav(db, "100018.OF", fund_nav.chain_adj_nav(
            fund_nav.parse_nav_history(_history([
                {"date": "2003-12-02", "nav": 1.0},
                {"date": "2026-09-18", "nav": 2.0, "percentage": 0.5},
            ]))
        ))
        count, first, last = fund_nav.fund_nav_stats(db, "100018.OF")
        assert (count, first, last) == (2, "2003-12-02", "2026-09-18")
