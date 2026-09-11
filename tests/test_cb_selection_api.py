# -*- coding: utf-8 -*-
"""统一执行与数据质量合同测试(T4, 方案 §3.2/§6.2)。

固定验收数据(§T4): A(110001, price100, redeem110, 行业760201)、
B(110002, price125, redeem110, 行业610101)、C(110003, price100, redeem缺失)。
模板"简单收益率 gte 0"只保留 A; B 原因 -12 不满足; C 原因赎回价缺失;
不配置该条件时 C 可出现在符合结果中; A 进黑名单后排除且原因可见。

覆盖:
- DB 与 live 同输入同结果(同一引擎, 只换数据来源);
- redeem_price 从真实 ORM 映射加载(CbRedeemDaily → redeem_map → enrich);
- 迁移 pending 模板拒绝执行(run 与 active 两个入口 409);
- 黑名单在执行入口生效并作为排除原因返回(不静默删除, 不破坏计数);
- 空快照 no_snapshot 与 0 只符合可区分(200 + meta.data_status);
- 赎回数据不可用 × 依赖模板 → 503 REDEEM_DATA_UNAVAILABLE, 无关模板可继续;
- 强赎跨日快照严格同日策略可见(D/R + 警告 + 字段按缺失处理);
- 计数分区: total_all = filtered + excluded, 每行只出现一次,
  blacklisted_count 与其他原因重叠不二次扣减;
- filter_only 无 selected/holdable 假徽标;
- 执行请求非法 → 422 且任何数据源 0 次调用(校验先于抓取);
- 行业目录 = 静态映射 + 快照发现, 不触网。
使用真实 ORM 数据 + 隔离内存库, 不访问外部网络。
"""
from __future__ import annotations

import json
from datetime import date

import pytest

BASE = "/api/cb-list"
D = date(2026, 9, 10)          # 行情快照交易日 D
R_PREV = date(2026, 9, 9)      # 跨日强赎快照 R(R≠D 用)


@pytest.fixture()
def cb_db(contract_client, thread_safe_engine):
    """与 contract_client 同一内存库的独立会话(供测试写 ORM 数据)。"""
    from sqlalchemy.orm import sessionmaker

    from backend.models.database import Base

    Base.metadata.create_all(bind=thread_safe_engine)
    TestSession = sessionmaker(bind=thread_safe_engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 固定验收数据(真实 ORM 行)
# ---------------------------------------------------------------------------
def _snapshot(bond_id: str, bond_nm: str, price: float, dblow: float,
              premium_rt: float, sw_cd: str) -> "CbDailySnapshot":
    from backend.models.valuation import CbDailySnapshot

    return CbDailySnapshot(
        trade_date=D, bond_id=bond_id, bond_nm=bond_nm,
        stock_nm="正常股份", price=price, dblow=dblow, premium_rt=premium_rt,
        sw_cd=sw_cd, rating_cd="AA", curr_iss_amt=5.0, year_left=2.0,
    )


def _acceptance_snapshots() -> list:
    return [
        _snapshot("110001", "A债", 100.0, 105.0, 5.0, "760201"),
        _snapshot("110002", "B债", 125.0, 140.0, 15.0, "610101"),
        _snapshot("110003", "C债", 100.0, 110.0, 10.0, "760201"),
    ]


def _redeem_row(bond_id: str, trade_date: date = D) -> "CbRedeemDaily":
    from backend.models.valuation import CbRedeemDaily

    return CbRedeemDaily(
        trade_date=trade_date, bond_id=bond_id,
        redeem_price=110.0, redeem_remain_days=200,
    )


def _seed_acceptance(cb_db, *, with_redeem: bool = True,
                     redeem_date: date = D) -> None:
    """写入 A/B/C 快照; with_redeem 时给 A/B 写赎回价 110(C 缺失)。"""
    for row in _acceptance_snapshots():
        cb_db.add(row)
    if with_redeem:
        cb_db.add(_redeem_row("110001", redeem_date))
        cb_db.add(_redeem_row("110002", redeem_date))
    cb_db.commit()


def _live_records() -> list[dict]:
    """与固定验收数据等价的实时 cell(同输入)。"""
    return [
        {"bond_id": "110001", "bond_nm": "A债", "stock_nm": "正常股份",
         "price": 100.0, "dblow": 105.0, "premium_rt": 5.0,
         "sw_cd": "760201", "rating_cd": "AA", "icons": {},
         "curr_iss_amt": 5.0, "year_left": 2.0},
        {"bond_id": "110002", "bond_nm": "B债", "stock_nm": "正常股份",
         "price": 125.0, "dblow": 140.0, "premium_rt": 15.0,
         "sw_cd": "610101", "rating_cd": "AA", "icons": {},
         "curr_iss_amt": 5.0, "year_left": 2.0},
        {"bond_id": "110003", "bond_nm": "C债", "stock_nm": "正常股份",
         "price": 100.0, "dblow": 110.0, "premium_rt": 10.0,
         "sw_cd": "760201", "rating_cd": "AA", "icons": {},
         "curr_iss_amt": 5.0, "year_left": 2.0},
    ]


def _live_redeem_cells() -> list[dict]:
    return [
        {"bond_id": "110001", "redeem_price": 110.0, "redeem_icon": None,
         "redeem_remain_days": 200, "redeem_real_days": "",
         "redeem_count_days": "", "redeem_total_days": ""},
        {"bond_id": "110002", "redeem_price": 110.0, "redeem_icon": None,
         "redeem_remain_days": 200, "redeem_real_days": "",
         "redeem_count_days": "", "redeem_total_days": ""},
    ]


# ---------------------------------------------------------------------------
# 模板构造
# ---------------------------------------------------------------------------
def _yield_template() -> dict:
    """简单收益率 gte 0(missing=exclude): 验收模板, 只保留 A。"""
    return {
        "id": "yield-only", "name": "简单收益率筛选", "description": "",
        "schema_version": 3, "source": "db",
        "conditions": [{
            "id": "c1", "field": "simple_maturity_yield_pct",
            "op": "gte", "value": 0, "enabled": True, "missing": "exclude",
        }],
        "strategy_factors": [
            {"field": "dblow", "ascending": True, "weight": 1, "enabled": True},
        ],
        "target_count": 10, "hold_tolerance": 0,
    }


def _config_entry(template: dict) -> dict:
    """执行请求模板 → 保存用 V3 模板条目(去掉执行专用顶层键)。"""
    return {k: v for k, v in template.items()
            if k not in ("schema_version", "source")}


def _reasons(excluded_row: dict) -> list[dict]:
    return excluded_row["exclude_reasons"]


# ---------------------------------------------------------------------------
# 必测
# ---------------------------------------------------------------------------
class TestUnifiedExecution:
    def test_db_live_same_input_same_selection(self, contract_client, cb_db):
        """§6.1: 业务输入相同则 DB 与 live 结果必须相同(同一引擎)。"""
        _seed_acceptance(cb_db)
        r_db = contract_client.post(f"{BASE}/screen", json=_yield_template())
        assert r_db.status_code == 200
        result_db = r_db.json()

        from unittest.mock import patch

        with patch(
            "backend.services.queries.live.fetch_live_snapshot",
            return_value=(_live_records(), _live_redeem_cells()),
        ) as fetch_mock:
            body = dict(_yield_template(), source="live")
            r_live = contract_client.post(f"{BASE}/screen", json=body)
        assert r_live.status_code == 200
        assert fetch_mock.call_count == 1
        result_live = r_live.json()

        assert result_db["selection_mode"] == result_live["selection_mode"] == "scored"
        assert result_db["rows"] == result_live["rows"]
        assert result_db["excluded_rows"] == result_live["excluded_rows"]
        assert result_db["total_all"] == result_live["total_all"] == 3
        # 只保留 A: 收益率 10%; B -12 与 C 缺失被排除
        assert [r["code"] for r in result_db["rows"]] == ["110001"]
        assert result_db["rows"][0]["simple_maturity_yield_pct"] == 10.0
        assert [r["code"] for r in result_db["excluded_rows"]] == ["110002", "110003"]
        # live 的 trade_date 不冒充(实时无法确认交易日 → null)
        assert result_db["meta"]["trade_date"] == "2026-09-10"
        assert result_live["meta"]["trade_date"] is None
        assert result_live["meta"]["fetched_at"]

    def test_db_redeem_price_loaded_from_orm(self, contract_client, cb_db):
        """redeem_price 必须从 CbRedeemDaily ORM 映射加载并参与公式。"""
        _seed_acceptance(cb_db)
        r = contract_client.post(f"{BASE}/screen", json=_yield_template())
        assert r.status_code == 200
        row = r.json()["rows"][0]
        assert row["code"] == "110001"
        assert row["redeem_price"] == 110.0  # 来自强赎快照 ORM 列
        assert row["simple_maturity_yield_pct"] == 10.0  # (110-100)/100*100
        assert row["redeem_gap"] == 10.0

        # B: (110-125)/125*100 = -12 → 原因可见且数值精确
        b = next(x for x in r.json()["excluded_rows"] if x["code"] == "110002")
        reason = _reasons(b)[0]
        assert reason["reason_code"] == "value_out_of_range"
        assert reason["actual"] == -12.0
        assert "-12" in reason["message"]
        # C: 赎回价缺失 → 收益率缺失, 原因说明根因
        c = next(x for x in r.json()["excluded_rows"] if x["code"] == "110003")
        reason_c = _reasons(c)[0]
        assert reason_c["reason_code"] == "missing"
        assert "赎回价" in reason_c["message"]

    def test_pending_legacy_yield_cannot_run(self, contract_client, cb_db, tmp_factors):
        """§5.2: 迁移 pending(旧 ytm_rt)模板两个执行入口都 409。"""
        _seed_acceptance(cb_db)
        pending_issue = {
            "id": "legacy-yield-1", "kind": "replaced_metric",
            "origin": "condition", "status": "pending",
            "original": {"field": "ytm_rt", "op": "lt", "threshold": 2},
            "replacement_field": "simple_maturity_yield_pct",
            "message": "旧集思录收益率条件需要确认",
        }
        body = dict(_yield_template(), migration_issues=[pending_issue])
        r = contract_client.post(f"{BASE}/screen", json=body)
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "TEMPLATE_REVIEW_REQUIRED"

        # archived(已确认/停用)条目不阻塞
        archived = dict(pending_issue, status="archived")
        r2 = contract_client.post(
            f"{BASE}/screen", json=dict(_yield_template(), migration_issues=[archived]))
        assert r2.status_code == 200

        # active 入口: 保存含 pending 的模板后 GET /screen/active 也 409
        save = contract_client.post(f"{BASE}/factors", json={
            "version": 3, "revision": "missing", "active_id": "yield-only",
            "templates": [_config_entry(dict(_yield_template(),
                                             migration_issues=[pending_issue]))],
        })
        assert save.status_code == 200
        r_active = contract_client.get(f"{BASE}/screen/active")
        assert r_active.status_code == 409
        assert r_active.json()["detail"]["code"] == "TEMPLATE_REVIEW_REQUIRED"

    def test_active_applies_blacklist(self, contract_client, cb_db, tmp_factors):
        """§6.3: active 执行生效黑名单, A 被排除且原因可见, 计数不被破坏。"""
        _seed_acceptance(cb_db)
        save = contract_client.post(f"{BASE}/factors", json={
            "version": 3, "revision": "missing", "active_id": "yield-only",
            "templates": [_config_entry(_yield_template())],
        })
        assert save.status_code == 200
        black = contract_client.post(
            f"{BASE}/blacklist", json={"bond_id": "110001", "bond_nm": "A债"})
        assert black.status_code == 200

        r = contract_client.get(f"{BASE}/screen/active")
        assert r.status_code == 200
        result = r.json()
        assert result["template_id"] == "yield-only"
        assert [x["code"] for x in result["rows"]] == []  # A 是唯一收益达标者
        a = next(x for x in result["excluded_rows"] if x["code"] == "110001")
        reasons = _reasons(a)
        # A 收益率达标(10 ≥ 0), 唯一排除原因是全局黑名单
        assert [x["rule_id"] for x in reasons] == ["global_blacklist"]
        assert reasons[0]["reason_code"] == "blacklisted"
        assert result["meta"]["blacklisted_count"] == 1
        # A(黑名单)与 B/C(模板条件)都只计一次
        assert result["total_all"] == 3
        assert result["total_filtered"] == 0
        assert result["total_excluded"] == 3

    def test_no_snapshot_distinct_from_no_matches(self, contract_client, cb_db):
        """§3.2: 无快照 → no_snapshot; 有快照 0 只符合 → ready, 两者可区分。"""
        r_empty = contract_client.post(f"{BASE}/screen", json=_yield_template())
        assert r_empty.status_code == 200
        empty = r_empty.json()
        assert empty["meta"]["data_status"] == "no_snapshot"
        assert empty["total_all"] == 0
        assert empty["rows"] == []

        _seed_acceptance(cb_db)
        matches_all = {
            "id": "price-only", "name": "价格筛选", "description": "",
            "schema_version": 3, "source": "db",
            "conditions": [{"id": "c1", "field": "price", "op": "lte",
                            "value": 130, "enabled": True}],
            "strategy_factors": [
                {"field": "dblow", "ascending": True, "weight": 1, "enabled": True}],
            "target_count": 10, "hold_tolerance": 0,
        }
        r_ready = contract_client.post(f"{BASE}/screen", json=matches_all)
        assert r_ready.status_code == 200
        ready = r_ready.json()
        assert ready["meta"]["data_status"] == "ready"
        assert ready["total_all"] == 3

        matches_none = {
            "id": "none", "name": "空筛选", "description": "",
            "schema_version": 3, "source": "db",
            "conditions": [{"id": "c1", "field": "price", "op": "gte",
                            "value": 999, "enabled": True}],
            "strategy_factors": [
                {"field": "dblow", "ascending": True, "weight": 1, "enabled": True}],
            "target_count": 10, "hold_tolerance": 0,
        }
        r_zero = contract_client.post(f"{BASE}/screen", json=matches_none)
        assert r_zero.status_code == 200
        zero = r_zero.json()
        assert zero["meta"]["data_status"] == "ready"
        assert zero["total_all"] == 3
        assert zero["total_filtered"] == 0
        assert zero["rows"] == []

    def test_redeem_unavailable_fails_dependent_template(self, contract_client, cb_db):
        """§3.2: 全市场赎回输入无效 × 依赖模板 → 503; 无关模板可继续+警告。"""
        _seed_acceptance(cb_db, with_redeem=False)
        r = contract_client.post(f"{BASE}/screen", json=_yield_template())
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "REDEEM_DATA_UNAVAILABLE"

        # 与赎回无关的模板继续执行, 只带质量警告
        independent = {
            "id": "price-only", "name": "价格筛选", "description": "",
            "schema_version": 3, "source": "db",
            "conditions": [{"id": "c1", "field": "price", "op": "lte",
                            "value": 130, "enabled": True}],
            "strategy_factors": [
                {"field": "dblow", "ascending": True, "weight": 1, "enabled": True}],
            "target_count": 10, "hold_tolerance": 0,
        }
        r2 = contract_client.post(f"{BASE}/screen", json=independent)
        assert r2.status_code == 200
        result = r2.json()
        assert result["total_filtered"] == 3
        assert result["meta"]["redeem_loaded"] is False
        assert result["meta"]["warnings"]
        assert all(row["redeem_price"] is None for row in result["rows"])
        assert all(
            row["simple_maturity_yield_pct"] is None for row in result["rows"]
        )

    def test_redeem_date_mismatch_is_visible(self, contract_client, cb_db):
        """§3.2 严格同日: R≠D 时 D/R 与警告可见, 赎回字段按缺失处理。"""
        _seed_acceptance(cb_db, with_redeem=True, redeem_date=R_PREV)
        independent = {
            "id": "price-only", "name": "价格筛选", "description": "",
            "schema_version": 3, "source": "db",
            "conditions": [{"id": "c1", "field": "price", "op": "lte",
                            "value": 130, "enabled": True}],
            "strategy_factors": [
                {"field": "dblow", "ascending": True, "weight": 1, "enabled": True}],
            "target_count": 10, "hold_tolerance": 0,
        }
        r = contract_client.post(f"{BASE}/screen", json=independent)
        assert r.status_code == 200
        meta = r.json()["meta"]
        assert meta["trade_date"] == "2026-09-10"
        assert meta["redeem_trade_date"] == "2026-09-09"
        assert meta["redeem_loaded"] is False
        assert any(
            "2026-09-09" in w and "2026-09-10" in w
            for w in meta["warnings"]
        )
        # 不拿跨日强赎计数冒充同日数据: 赎回字段一律缺失
        for row in r.json()["rows"]:
            assert row["redeem_price"] is None
            assert row["simple_maturity_yield_pct"] is None

        # 同一情形下依赖收益率的模板明确失败
        r2 = contract_client.post(f"{BASE}/screen", json=_yield_template())
        assert r2.status_code == 503
        assert r2.json()["detail"]["code"] == "REDEEM_DATA_UNAVAILABLE"

    def test_selection_counts_partition_all_rows(self, contract_client, cb_db):
        """§6.2: total_all = filtered + excluded; 每行只计一次; 黑名单重叠不扣减。"""
        _seed_acceptance(cb_db)
        contract_client.post(
            f"{BASE}/blacklist", json={"bond_id": "110002", "bond_nm": "B债"})
        template = {
            "id": "price-only", "name": "价格筛选", "description": "",
            "schema_version": 3, "source": "db",
            "conditions": [{"id": "c1", "field": "price", "op": "lte",
                            "value": 120, "enabled": True}],  # B(125) 违反
            "strategy_factors": [
                {"field": "dblow", "ascending": True, "weight": 1, "enabled": True}],
            "target_count": 10, "hold_tolerance": 0,
        }
        r = contract_client.post(f"{BASE}/screen", json=template)
        assert r.status_code == 200
        result = r.json()
        assert result["total_all"] == 3
        assert result["total_filtered"] + result["total_excluded"] == 3
        row_codes = [x["code"] for x in result["rows"]]
        excluded_codes = [x["code"] for x in result["excluded_rows"]]
        assert set(row_codes) | set(excluded_codes) == {
            "110001", "110002", "110003"}
        assert not set(row_codes) & set(excluded_codes)
        # B 同时命中黑名单与模板条件: 原因合并, 只出现一次
        b = next(x for x in result["excluded_rows"] if x["code"] == "110002")
        rule_ids = {x["rule_id"] for x in _reasons(b)}
        assert rule_ids == {"global_blacklist", "c1"}
        assert result["meta"]["blacklisted_count"] == 1
        assert result["total_all"] == result["total_filtered"] + result["total_excluded"]
        # 入选/缓冲不越过通过集合
        assert result["selected_count"] <= result["total_filtered"]
        assert result["selected_count"] + result["buffer_count"] <= result["total_filtered"]

    def test_filter_only_has_no_false_selected_badges(self, contract_client, cb_db):
        """无启用评分因子 → filter_only: 不打分不标记入选, 双低升序。"""
        _seed_acceptance(cb_db)
        template = {
            "id": "filter-only", "name": "纯过滤", "description": "",
            "schema_version": 3, "source": "db",
            "conditions": [{"id": "c1", "field": "price", "op": "lte",
                            "value": 130, "enabled": True}],
            "strategy_factors": [],
            "target_count": 10, "hold_tolerance": 0,
        }
        r = contract_client.post(f"{BASE}/screen", json=template)
        assert r.status_code == 200
        result = r.json()
        assert result["selection_mode"] == "filter_only"
        assert result["top_n"] == 0
        assert result["keep_n"] == 0
        assert result["selected_count"] == 0
        assert result["buffer_count"] == 0
        assert [x["code"] for x in result["rows"]] == ["110001", "110003", "110002"]
        for rank, row in enumerate(result["rows"], 1):
            assert row["rank"] == rank
            assert row["selected"] is False
            assert row["holdable"] is False
            assert row["total_score"] is None
        # C(无赎回价)不配收益率条件时出现在符合结果中(§T4 验收)
        assert "110003" in [x["code"] for x in result["rows"]]


# ---------------------------------------------------------------------------
# 校验先于抓取(HTTP 层, 批次 1 遗留项升级)
# ---------------------------------------------------------------------------
class TestInvalidInputPreventsFetch:
    def test_invalid_run_payload_rejected_without_fetch(self, contract_client, cb_db):
        """§4.3: 非法执行请求 422({code,message,path,errors}), 数据源 0 次调用。"""
        from unittest.mock import patch

        _seed_acceptance(cb_db)
        bad_payloads = [
            dict(_yield_template(), conditions=[
                {"id": "c1", "field": "nonexistent_field", "op": "gte",
                 "value": 1, "enabled": True}]),
            dict(_yield_template(), conditions=[
                # 字符串数字同样被 strict 校验拒绝(等价于 NaN 文本的拒绝路径)
                {"id": "c1", "field": "price", "op": "gte",
                 "value": "NaN", "enabled": True}]),
            dict(_yield_template(), source="jisilu"),
            dict(_yield_template(), schema_version=2),
        ]
        with patch(
            "backend.services.queries.live.fetch_live_snapshot",
        ) as fetch_mock:
            for payload in bad_payloads:
                r = contract_client.post(f"{BASE}/screen", json=payload)
                assert r.status_code == 422, payload
                detail = r.json()["detail"]
                assert detail["code"] == "INVALID_CONFIG"
                assert detail["message"]
                assert detail["path"]
                assert detail["errors"]
            assert fetch_mock.call_count == 0


# ---------------------------------------------------------------------------
# 行业目录(§3.3/§6.2)
# ---------------------------------------------------------------------------
class TestIndustriesEndpoint:
    def test_industries_combines_catalog_and_snapshot(self, contract_client, cb_db):
        """静态映射 + 快照发现: 回退标注 fallback, 未知原始码保留(未映射)。"""
        from backend.models.valuation import CbDailySnapshot

        cb_db.add(_snapshot("110001", "A债", 100.0, 105.0, 5.0, "760201"))
        cb_db.add(_snapshot("110002", "B债", 125.0, 140.0, 15.0, "610101"))
        cb_db.add(CbDailySnapshot(
            trade_date=D, bond_id="110004", bond_nm="D债",
            price=100.0, sw_cd="999999",
        ))
        cb_db.commit()

        r = contract_client.get(f"{BASE}/factors/industries")
        assert r.status_code == 200
        entries = r.json()
        by_code = {e["industry_code"]: e for e in entries}

        # 静态三级码: catalog 来源
        assert by_code["760201"]["source"] == "catalog"
        assert by_code["760201"]["industry_is_fallback"] is False
        assert by_code["760201"]["industry_name"]
        # 三级码缺失回退二级: fallback 标注 + 名称(水泥)
        assert by_code["610101"]["industry_is_fallback"] is True
        assert by_code["610101"]["industry_mapped_code"] == "610100"
        assert by_code["610101"]["industry_name"] == "水泥"
        # 快照发现的未知原始码: 保留选项, 名称留空(前端显示未映射)
        assert by_code["999999"]["source"] == "snapshot"
        assert by_code["999999"]["industry_name"] is None
        # 按代码升序, 不触网(整个测试套件有 socket 拦截兜底)
        codes = [e["industry_code"] for e in entries]
        assert codes == sorted(codes)
