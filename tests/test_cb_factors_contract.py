# -*- coding: utf-8 -*-
"""策略配置与评级目录合同测试(P2-R02/R04/R07)。

覆盖:
- "AAA" 字符串不会被保存成 ["A"](P2-R02 核心反例)
- ratings 非字符串数组/含空串 → 422
- NONE 只表示缺失评级; 空数组 = 不限
- 评级目录含规范 14 项 + 快照发现的未知值
- 旧配置 excluded_ratings 兼容只在读取时生效
不访问真实数据源。
"""
from __future__ import annotations

import json

import pytest

BASE = "/api/cb-list"


@pytest.fixture()
def thread_db(contract_client, thread_safe_engine):
    """与 contract_client 同一内存库的独立会话(供测试写数据)。"""
    from sqlalchemy.orm import sessionmaker

    from backend.models.database import Base

    Base.metadata.create_all(bind=thread_safe_engine)
    TestSession = sessionmaker(bind=thread_safe_engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


def _tmpl(**overrides):
    base = {
        "id": "t1", "name": "测试策略",
        "target_count": 5, "hold_tolerance": 0,
        "exclusion_rules": [], "strategy_factors": [],
        "excluded_redeem_icons": [], "redeem_safe_days": 2,
        "excluded_bond_codes": [], "min_listing_days": 0,
        "ratings": ["AAA", "AA"],
    }
    base.update(overrides)
    return base


class TestSaveFactorsContract:
    def test_string_rating_not_split_to_chars(self, contract_client, tmp_factors):
        """P2-R02 核心反例: ratings:"AAA" 必须 422, 不能被按字符迭代存成 ["A"]。"""
        r = contract_client.post(f"{BASE}/factors", json={
            "active_id": "t1", "templates": [_tmpl(ratings="AAA")],
        })
        assert r.status_code == 422

    def test_object_rating_rejected(self, contract_client, tmp_factors):
        r = contract_client.post(f"{BASE}/factors", json={
            "active_id": "t1", "templates": [_tmpl(ratings={"AAA": True})],
        })
        assert r.status_code == 422

    def test_empty_string_rating_rejected(self, contract_client, tmp_factors):
        r = contract_client.post(f"{BASE}/factors", json={
            "active_id": "t1", "templates": [_tmpl(ratings=["AA", "  "])],
        })
        assert r.status_code == 422

    def test_valid_ratings_saved(self, contract_client, tmp_factors):
        r = contract_client.post(f"{BASE}/factors", json={
            "active_id": "t1", "templates": [_tmpl(ratings=["aa+", "NONE"])],
        })
        assert r.status_code == 200
        saved = r.json()["data"]["templates"][0]["ratings"]
        assert saved == ["AA+", "NONE"]  # 大写规范化; 写盘前由 _normalize_templates 排序

    def test_empty_ratings_means_unrestricted(self, contract_client, tmp_factors):
        r = contract_client.post(f"{BASE}/factors", json={
            "active_id": "t1", "templates": [_tmpl(ratings=[])],
        })
        assert r.status_code == 200
        assert r.json()["data"]["templates"][0]["ratings"] == []

    def test_missing_templates_rejected(self, contract_client, tmp_factors):
        r = contract_client.post(f"{BASE}/factors", json={"active_id": "t1"})
        assert r.status_code == 422


class TestR3Contracts:
    """R3-02/R3-05: 新请求与旧文件的评级边界。"""

    def test_missing_ratings_round_trip_is_unrestricted(self, contract_client, tmp_factors):
        """R3-02 反例: 新 POST 缺省 ratings 保存为 [](不限), 不落回旧七档迁移。"""
        tmpl = _tmpl()
        del tmpl["ratings"]  # 模拟前端不传 ratings 字段
        r = contract_client.post(f"{BASE}/factors", json={
            "active_id": "t1", "templates": [tmpl],
        })
        assert r.status_code == 200
        # 响应值
        assert r.json()["data"]["templates"][0]["ratings"] == []
        # 文件内容
        on_disk = json.loads(tmp_factors.read_text(encoding="utf-8"))
        assert on_disk["templates"][0]["ratings"] == []
        # 再次 GET 一致(V3 形态: 不限评级 = 无 rating_cd 条件, 不回旧七档)
        r2 = contract_client.get(f"{BASE}/factors")
        migrated = r2.json()["templates"][0]
        assert not [c for c in migrated["conditions"] if c["field"] == "rating_cd"]

    def test_null_ratings_rejected(self, contract_client, tmp_factors):
        """R3-02: null ratings 按合同返回 422(不是静默转 [] 并触发旧迁移)。"""
        r = contract_client.post(f"{BASE}/factors", json={
            "active_id": "t1", "templates": [_tmpl(ratings=None)],
        })
        assert r.status_code == 422

    def test_new_post_rejects_legacy_exclusions(self, contract_client, tmp_factors):
        """R3-02: 新 POST 携带旧字段 excluded_ratings 返回 422(兼容只在读旧文件)。"""
        r = contract_client.post(f"{BASE}/factors", json={
            "active_id": "t1",
            "templates": [_tmpl(ratings=["AA"], excluded_ratings=["BB"])],
        })
        assert r.status_code == 422

    def test_exact_duplicate_ratings_rejected(self, contract_client, tmp_factors):
        """R3-05: 完全重复的评级 422(不是静默去重)。"""
        r = contract_client.post(f"{BASE}/factors", json={
            "active_id": "t1", "templates": [_tmpl(ratings=["AAA", "AAA"])],
        })
        assert r.status_code == 422

    def test_normalized_duplicate_ratings_rejected(self, contract_client, tmp_factors):
        """R3-05: 大小写/空白归一后重复也 422。"""
        r = contract_client.post(f"{BASE}/factors", json={
            "active_id": "t1", "templates": [_tmpl(ratings=["AAA", " aaa "])],
        })
        assert r.status_code == 422
        # 422 请求不得改文件
        assert not tmp_factors.exists()

    def test_legacy_read_migration_idempotent(self, tmp_factors):
        """旧文件读取迁移连续两次结果一致。"""
        from backend.services import cb_factors

        legacy = {
            "version": 1, "active_id": "t1",
            "templates": [_tmpl(ratings=None, excluded_ratings=["BB"])],
        }
        tmp_factors.write_text(json.dumps(legacy), encoding="utf-8")
        first = cb_factors.read_config()
        # 把第一次读取的结果写回, 再读第二次
        tmp_factors.write_text(json.dumps(first), encoding="utf-8")
        second = cb_factors.read_config()
        assert first["templates"][0]["ratings"] == second["templates"][0]["ratings"]

    def test_legacy_get_response_can_be_posted_as_current_config(self, contract_client, tmp_factors):
        """R4-01(修订到 V3): GET 旧配置 → 原样 POST(V3+revision)完整往返。

        排除语义反例保持: excluded_ratings=["AA"] 经 GET 迁移为
        rating_cd in 条件(不含 AA, missing=exclude 语义), POST 后落盘
        V3 且不含旧字段 excluded_ratings。
        """
        legacy = {
            "version": 1, "active_id": "t1",
            "templates": [_tmpl(ratings=None, excluded_ratings=["AA"])],
        }
        tmp_factors.write_text(json.dumps(legacy), encoding="utf-8")

        loaded = contract_client.get(f"{BASE}/factors")
        assert loaded.status_code == 200
        template = loaded.json()["templates"][0]
        rating_cond = next(
            c for c in template["conditions"] if c["field"] == "rating_cd"
        )
        assert rating_cond["op"] == "in"
        assert rating_cond["value"] == ["A", "A+", "A-", "AA+", "AA-", "AAA"]
        assert rating_cond["missing"] == "exclude"  # 旧白名单连缺失评级一起排除
        assert "excluded_ratings" not in template

        saved = contract_client.post(f"{BASE}/factors", json=loaded.json())
        assert saved.status_code == 200
        on_disk = json.loads(tmp_factors.read_text(encoding="utf-8"))
        assert on_disk["version"] == 3
        assert "excluded_ratings" not in on_disk["templates"][0]
        disk_rating_cond = next(
            c for c in on_disk["templates"][0]["conditions"]
            if c["field"] == "rating_cd"
        )
        assert disk_rating_cond == rating_cond  # 评级语义逐字段保持


class TestReadConfigLegacy:
    def test_legacy_excluded_ratings_inverted_on_read(self, tmp_factors, monkeypatch):
        """旧配置(排除语义)只在读取迁移时反转, 不用于新 POST。"""
        from backend.services import cb_factors

        legacy = {
            "version": 1, "active_id": "t1",
            "templates": [_tmpl(ratings=None, excluded_ratings=["BB"])],
        }
        tmp_factors.write_text(json.dumps(legacy), encoding="utf-8")
        cfg = cb_factors.read_config()
        ratings = cfg["templates"][0]["ratings"]
        assert "BB" not in ratings
        assert "AAA" in ratings  # 反转后保留其余


class TestRatingCatalog:
    def test_catalog_contains_14_standard_entries(self, contract_client):
        r = contract_client.get(f"{BASE}/factors/ratings")
        assert r.status_code == 200
        catalog = r.json()
        values = [e["value"] for e in catalog]
        assert len(values) == 14
        assert values[:3] == ["AAA", "AA+", "AA"]
        none_entry = next(e for e in catalog if e["value"] == "NONE")
        assert none_entry["is_missing"] is True

    def test_catalog_discovers_unknown_values(self, contract_client, thread_db):
        """快照中出现未登记评级(如 BB+)时出现在目录末尾。"""
        from datetime import date

        from backend.models.valuation import CbDailySnapshot

        thread_db.add(CbDailySnapshot(
            trade_date=date(2026, 9, 8), bond_id="110099", bond_nm="新债",
            rating_cd="BB+", price=100.0,
        ))
        thread_db.commit()
        r = contract_client.get(f"{BASE}/factors/ratings")
        assert r.status_code == 200
        values = [e["value"] for e in r.json()]
        assert "BB+" in values
        assert values[-1] == "BB+"  # 未知值排末尾
