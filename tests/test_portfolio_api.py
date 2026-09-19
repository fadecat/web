# -*- coding: utf-8 -*-
"""组合端点测试(P2): L1 列表 / L2 详情 / 新建(含复制) / PATCH 改成员 / 软删。

契约客户端(test conftest 的 contract_client) + 内存库, 不触网。
"""
from __future__ import annotations

import pytest

from backend.services import research_store

BASE = "/api/portfolio/portfolios"


@pytest.fixture()
def seeded(contract_client, thread_db):
    """注册两只标的供组合使用(经 research_store, 不走抓取)。"""
    research_store.upsert_securities(thread_db, [
        {"symbol": "600900.SH", "name": "长江电力", "type": "stock",
         "source": "tencent", "selection_list": "组合实验室"},
        {"symbol": "100018.OF", "name": "富国天利增长债券A", "type": "fund",
         "source": "danjuan", "selection_list": "组合实验室"},
    ])
    return thread_db


class TestPortfolioCrudEndpoints:
    def test_create_returns_201_with_default_name(self, contract_client) -> None:
        r = contract_client.post(BASE, json={})
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "我的组合1"
        assert body["status"] == "active"
        assert body["asset_count"] == 0
        # 空组合三格为空(None) → 前端渲染为 —
        assert body["cached_day_return"] is None
        assert body["cached_asof_date"] is None

    def test_create_with_name_and_rebalance(self, contract_client) -> None:
        r = contract_client.post(BASE, json={"name": "四资产等权", "default_rebalance": "yearly"})
        assert r.status_code == 201
        assert r.json()["default_rebalance"] == "yearly"

    def test_create_with_bad_rebalance_returns_422(self, contract_client) -> None:
        r = contract_client.post(BASE, json={"default_rebalance": "monthly"})
        assert r.status_code == 422

    def test_list_returns_created(self, contract_client) -> None:
        contract_client.post(BASE, json={"name": "A"})
        contract_client.post(BASE, json={"name": "B"})
        body = contract_client.get(BASE).json()
        assert {p["name"] for p in body} == {"A", "B"}

    def test_detail_of_unknown_returns_404(self, contract_client) -> None:
        assert contract_client.get(f"{BASE}/999999").status_code == 404

    def test_delete_is_soft_and_recoverable(self, contract_client) -> None:
        pid = contract_client.post(BASE, json={"name": "待删"}).json()["id"]
        assert contract_client.delete(f"{BASE}/{pid}").json()["status"] == "archived"
        assert contract_client.get(BASE).json() == []
        assert len(contract_client.get(BASE, params={"include_archived": True}).json()) == 1

    def test_rename_via_patch(self, contract_client) -> None:
        pid = contract_client.post(BASE, json={"name": "旧名"}).json()["id"]
        r = contract_client.patch(f"{BASE}/{pid}", json={"name": "新名"})
        assert r.status_code == 200
        assert r.json()["name"] == "新名"

    def test_patch_blank_name_returns_422(self, contract_client) -> None:
        pid = contract_client.post(BASE, json={}).json()["id"]
        assert contract_client.patch(f"{BASE}/{pid}", json={"name": "   "}).status_code == 422

    def test_copy_carries_members(self, contract_client, seeded) -> None:
        pid = contract_client.post(BASE, json={"name": "原组合"}).json()["id"]
        contract_client.patch(f"{BASE}/{pid}", json={
            "assets": [{"symbol": "600900.SH", "target_weight": 100.0}],
        })
        copied = contract_client.post(BASE, json={"from_id": pid}).json()
        assert copied["name"] == "复制_原组合"
        assert copied["asset_count"] == 1
        detail = contract_client.get(f"{BASE}/{copied['id']}").json()
        assert detail["assets"][0]["symbol"] == "600900.SH"
        assert detail["ready"] is True


class TestPortfolioAssetsEndpoint:
    def test_patch_replaces_members_and_weights(self, contract_client, seeded) -> None:
        pid = contract_client.post(BASE, json={}).json()["id"]
        r = contract_client.patch(f"{BASE}/{pid}", json={"assets": [
            {"symbol": "600900.SH", "target_weight": 60.0},
            {"symbol": "100018.OF", "target_weight": 40.0},
        ]})
        assert r.status_code == 200
        body = r.json()
        assert [a["symbol"] for a in body["assets"]] == ["600900.SH", "100018.OF"]
        assert body["weight_sum"] == 100.0
        assert body["ready"] is True
        # 逐个成员带口径/类型(页面逐列标注要用)
        types = {a["symbol"]: a["security_type"] for a in body["assets"]}
        assert types == {"600900.SH": "STOCK", "100018.OF": "FUND"}

    def test_patch_removes_member_not_in_list(self, contract_client, seeded) -> None:
        pid = contract_client.post(BASE, json={}).json()["id"]
        contract_client.patch(f"{BASE}/{pid}", json={"assets": [
            {"symbol": "600900.SH", "target_weight": 100.0},
        ]})
        body = contract_client.patch(f"{BASE}/{pid}", json={"assets": []}).json()
        assert body["assets"] == []
        assert body["ready"] is False

    def test_unregistered_symbol_rejected(self, contract_client) -> None:
        pid = contract_client.post(BASE, json={}).json()["id"]
        r = contract_client.patch(f"{BASE}/{pid}", json={"assets": [{"symbol": "513100.SH"}]})
        assert r.status_code == 422
        assert "未注册" in r.json()["detail"]

    def test_duplicate_symbol_in_payload_rejected(self, contract_client, seeded) -> None:
        pid = contract_client.post(BASE, json={}).json()["id"]
        r = contract_client.patch(f"{BASE}/{pid}", json={"assets": [
            {"symbol": "600900.SH", "target_weight": 50.0},
            {"symbol": "600900.SH", "target_weight": 50.0},
        ]})
        assert r.status_code == 422
        assert "重复" in r.json()["detail"]

    def test_partial_weights_not_ready(self, contract_client, seeded) -> None:
        pid = contract_client.post(BASE, json={}).json()["id"]
        body = contract_client.patch(f"{BASE}/{pid}", json={"assets": [
            {"symbol": "600900.SH", "target_weight": 40.0},
            {"symbol": "100018.OF", "target_weight": None},
        ]}).json()
        assert body["ready"] is False
        assert body["weight_sum"] == 40.0

    def test_readiness_reports_missing_data_with_reason(self, contract_client, seeded) -> None:
        """无数据时不阻塞: 逐成员给 reason + 共同起点为 None(前端给「立即同步」按钮)。"""
        pid = contract_client.post(BASE, json={}).json()["id"]
        body = contract_client.patch(f"{BASE}/{pid}", json={"assets": [
            {"symbol": "600900.SH", "target_weight": 100.0},
        ]}).json()
        readiness = body["data_readiness"]
        assert readiness["all_ready"] is False
        assert readiness["common_start"] is None
        assert readiness["assets"][0]["reason"] == "无数据, 请先同步"

    def test_detail_exposes_since_added_return_field(self, contract_client, seeded) -> None:
        """「添加后的收益」是纯展示列: 无数据时给 None, 不能用权重冒充。"""
        pid = contract_client.post(BASE, json={}).json()["id"]
        body = contract_client.patch(f"{BASE}/{pid}", json={"assets": [
            {"symbol": "600900.SH", "target_weight": 100.0},
        ]}).json()
        member = body["assets"][0]
        assert "since_added_return" in member
        assert member["since_added_return"] is None  # 无行情 → 无法计算
        assert member["current_weight"] is None      # 漂移权重属 P3 账本, 不用目标权重冒充
        assert member["target_weight"] == 100.0
