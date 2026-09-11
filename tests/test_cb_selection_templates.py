# -*- coding: utf-8 -*-
"""模板迁移、重命名与文件保存测试(T3, 方案 §5)。

覆盖:
- migrate_config_to_v3 纯函数: 深拷贝零磁盘 IO、确定性、幂等(迁移两次完全一致)、
  §5.1 全部映射(V1 评级前置归一/lt-gt 反转/评级/强赎标记/安全天数/上市天数/
  排除代码/ST 显式化)、§5.2 ytm_rt pending(条件与评分)、重名追加"(迁移N)"、
  缺失/重复 ID 稳定迁移 ID、active_id 重置、迁移结果可通过 V3 schema;
- GET /cb-list/factors: 只内存迁移不改磁盘、revision=磁盘字节 SHA256、
  无文件 revision=missing、坏文件 503 CONFIG_UNREADABLE 不回默认;
- POST /cb-list/factors(V3): revision 冲突 409(两个标签同一 revision)、
  首次落盘独占备份且逐字节一致、os.replace 失败原文件字节不变、
  空白名/41字符/归一重名 422 且文件不变、重命名只改 name、
  同名带空格重命名无实质变动;
- V1/V2 旧客户端兼容: 磁盘 V1/V2 或无文件时可保存, 磁盘 V3 后 409
  CLIENT_UPGRADE_REQUIRED。
不访问真实数据源, factors.json 由 tmp_factors 重定向到测试目录。
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from backend.services.cb_factors import DEFAULT_CONFIG
from backend.services.cb_template_migration import migrate_config_to_v3
from backend.api.schemas.cb_screen import SelectionTemplateModel

BASE = "/api/cb-list"


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------
def _legacy_template(**overrides):
    """V1/V2 形态模板(旧字段齐全)。"""
    base = {
        "id": "t1", "name": "旧策略",
        "target_count": 5, "hold_tolerance": 0,
        "exclusion_rules": [
            {"field": "pb", "label": "市净率", "op": "lt", "threshold": 1, "enabled": True},
            {"field": "curr_iss_amt", "label": "剩余规模", "op": "gt", "threshold": 20, "enabled": True},
        ],
        "strategy_factors": [
            {"field": "dblow", "label": "双低值", "ascending": True, "weight": 1.0, "enabled": True},
        ],
        "excluded_redeem_icons": ["R", "O"],
        "redeem_safe_days": 2,
        "excluded_bond_codes": ["110001.SH"],
        "ratings": ["AA+", "AA"],
        "min_listing_days": 0,
    }
    base.update(overrides)
    return base


def _v3_template(**overrides):
    base = {
        "id": "stable", "name": "稳健筛选",
        "description": "",
        "conditions": [
            {"id": "c1", "field": "price", "op": "between", "value": [80, 120],
             "enabled": True, "missing": "exclude"},
        ],
        "strategy_factors": [
            {"field": "dblow", "ascending": True, "weight": 1, "enabled": True},
        ],
        "target_count": 10, "hold_tolerance": 0,
        "migration_issues": [],
    }
    base.update(overrides)
    return base


def _v3_config(**overrides):
    base = {
        "version": 3, "revision": "missing",
        "active_id": "stable", "templates": [_v3_template()],
    }
    base.update(overrides)
    return base


def _find_condition(template: dict, field: str) -> dict | None:
    for cond in template.get("conditions", []):
        if cond.get("field") == field:
            return cond
    return None


def _issues_of(template: dict, kind: str) -> list[dict]:
    return [i for i in template.get("migration_issues", []) if i.get("kind") == kind]


# ---------------------------------------------------------------------------
# migrate_config_to_v3 纯函数
# ---------------------------------------------------------------------------
class TestMigrationPure:
    def test_migrate_twice_fully_identical(self):
        """确定性 + 幂等: 两次迁移结果一致, 且对迁移结果再迁移不再变化。"""
        config = {"version": 2, "active_id": "t1", "templates": [_legacy_template()]}
        first = migrate_config_to_v3(config)
        second = migrate_config_to_v3(config)
        assert first == second
        assert migrate_config_to_v3(first) == first  # 幂等不动点

    def test_input_not_mutated(self):
        config = {"version": 2, "active_id": "t1", "templates": [_legacy_template()]}
        snapshot = copy.deepcopy(config)
        migrate_config_to_v3(config)
        assert config == snapshot  # 深拷贝入参, 不原地污染

    def test_migrate_does_no_disk_io(self, tmp_factors):
        """零磁盘 IO: 迁移不创建/不修改任何文件。"""
        assert not tmp_factors.exists()
        migrate_config_to_v3(DEFAULT_CONFIG)
        assert not tmp_factors.exists()
        assert not list(tmp_factors.parent.glob("factors.pre-v3.*"))

    def test_ytm_condition_and_scoring_both_pending(self):
        """§5.2: 启用的 ytm 条件与评分都产生 pending, 移出可执行列表。"""
        template = _legacy_template(
            exclusion_rules=[
                {"field": "ytm_rt", "label": "收益率", "op": "lt", "threshold": 2, "enabled": True},
                {"field": "ytm_rt", "label": "收益率", "op": "lt", "threshold": 1, "enabled": False},
            ],
            strategy_factors=[
                {"field": "ytm_rt", "ascending": False, "weight": 1.0, "enabled": True},
            ],
        )
        migrated = migrate_config_to_v3(
            {"version": 2, "active_id": "t1", "templates": [template]}
        )["templates"][0]
        replaced = _issues_of(migrated, "replaced_metric")
        assert len(replaced) == 3
        statuses = sorted(i["status"] for i in replaced)
        assert statuses == ["archived", "pending", "pending"]
        origins = sorted(i["origin"] for i in replaced)
        assert origins == ["condition", "condition", "scoring_factor"]
        for issue in replaced:
            assert issue["replacement_field"] == "simple_maturity_yield_pct"
            assert issue["original"]["field"] == "ytm_rt"
        # 旧条目已移出可执行列表
        assert not [c for c in migrated["conditions"] if c["field"] == "ytm_rt"]
        assert not [f for f in migrated["strategy_factors"] if f["field"] == "ytm_rt"]

    def test_v1_ratings_pre_normalization(self):
        """V1 excluded_ratings 先反转为保留语义, 再映射 rating_cd in。"""
        template = _legacy_template(ratings=None, excluded_ratings=["BB", "AA"])
        migrated = migrate_config_to_v3(
            {"version": 1, "active_id": "t1", "templates": [template]}
        )["templates"][0]
        cond = _find_condition(migrated, "rating_cd")
        assert cond is not None and cond["op"] == "in"
        assert "AA" not in cond["value"]
        assert "AAA" in cond["value"]
        assert "BB" not in cond["value"]
        assert "excluded_ratings" not in migrated

    def test_empty_ratings_no_rating_condition(self):
        """ratings 空(不限)不生成评级条件; 未知评级原样保留。"""
        template = _legacy_template(ratings=[], excluded_ratings=None)
        migrated = migrate_config_to_v3(
            {"version": 2, "active_id": "t1", "templates": [template]}
        )["templates"][0]
        assert _find_condition(migrated, "rating_cd") is None

        template2 = _legacy_template(ratings=["AA+", "weird"], excluded_ratings=None)
        migrated2 = migrate_config_to_v3(
            {"version": 2, "active_id": "t1", "templates": [template2]}
        )["templates"][0]
        cond = _find_condition(migrated2, "rating_cd")
        assert cond["value"] == ["AA+", "WEIRD"]

    def test_exclusion_rules_boundary_and_missing(self):
        """lt→gte / gt→lte(相等边界保留), missing=include(旧缺失放行)。"""
        migrated = migrate_config_to_v3(
            {"version": 2, "active_id": "t1", "templates": [_legacy_template()]}
        )["templates"][0]
        pb = _find_condition(migrated, "pb")
        assert pb["op"] == "gte" and pb["value"] == 1 and pb["missing"] == "include"
        amt = _find_condition(migrated, "curr_iss_amt")
        assert amt["op"] == "lte" and amt["value"] == 20

    def test_safe_days_negative_include_and_listing_days(self):
        """redeem_safe_days≥0 → gt + missing/negative include; min_listing_days 0 不生成。"""
        migrated = migrate_config_to_v3(
            {"version": 2, "active_id": "t1", "templates": [_legacy_template()]}
        )["templates"][0]
        safe = _find_condition(migrated, "trigger_days_remaining")
        assert safe["op"] == "gt" and safe["value"] == 2
        assert safe["missing"] == "include" and safe["negative"] == "compare"
        assert _find_condition(migrated, "listed_days") is None  # 0 不生成

        migrated2 = migrate_config_to_v3({"version": 2, "active_id": "t1", "templates": [
            _legacy_template(min_listing_days=30, redeem_safe_days=None),
        ]})["templates"][0]
        listed = _find_condition(migrated2, "listed_days")
        assert listed["op"] == "gte" and listed["value"] == 30
        assert listed["missing"] == "include"
        assert _find_condition(migrated2, "redeem_remain_days") is None

    def test_icons_default_and_explicit_empty(self):
        """缺省按旧逻辑 R/O/B; 显式[]不限; 非法标记留痕忽略。"""
        migrated = migrate_config_to_v3({"version": 2, "active_id": "t1", "templates": [
            _legacy_template(excluded_redeem_icons=None),
        ]})["templates"][0]
        assert _find_condition(migrated, "redeem_icons") is None
        assert _issues_of(migrated, "redeem_semantics")[0]["original"]["value"] == ["R", "O", "B"]
        assert _issues_of(migrated, "redeem_semantics")[0]["status"] == "pending"

        migrated2 = migrate_config_to_v3({"version": 2, "active_id": "t1", "templates": [
            _legacy_template(excluded_redeem_icons=[]),
        ]})["templates"][0]
        assert _find_condition(migrated2, "redeem_icons") is None

        migrated3 = migrate_config_to_v3({"version": 2, "active_id": "t1", "templates": [
            _legacy_template(excluded_redeem_icons=["R", "X"]),
        ]})["templates"][0]
        assert _issues_of(migrated3, "redeem_semantics")[0]["original"]["value"] == ["R"]
        assert _issues_of(migrated3, "invalid_value_dropped")

    def test_excluded_codes_normalized_and_st_added(self):
        """排除代码归一 6 位; 每个旧模板显式生成 stock_is_st eq false。"""
        migrated = migrate_config_to_v3(
            {"version": 2, "active_id": "t1", "templates": [_legacy_template()]}
        )["templates"][0]
        code_cond = _find_condition(migrated, "code")
        assert code_cond["op"] == "not_in" and code_cond["value"] == ["110001"]
        st = _find_condition(migrated, "stock_is_st")
        assert st["op"] == "eq" and st["value"] is False and st["enabled"] is True

    def test_unmappable_rule_visible(self):
        """未知字段/未知运算符/非法阈值必须产生可见迁移问题, 不无声丢弃。"""
        template = _legacy_template(exclusion_rules=[
            {"field": "turnover_rt", "op": "lt", "threshold": 5, "enabled": True},
            {"field": "price", "op": "between", "threshold": [80, 120], "enabled": False},
            {"field": "price", "op": "lt", "threshold": "abc", "enabled": True},
            {"field": "price", "op": "lt", "threshold": -3, "enabled": True},  # 负阈值无效规则
        ])
        migrated = migrate_config_to_v3(
            {"version": 2, "active_id": "t1", "templates": [template]}
        )["templates"][0]
        unmappable = _issues_of(migrated, "unmappable_rule")
        statuses = sorted(i["status"] for i in unmappable)
        assert statuses == ["archived", "pending", "pending"]
        assert not [c for c in migrated["conditions"] if c["field"] == "turnover_rt"]
        invalid = _issues_of(migrated, "invalid_value_dropped")
        assert len(invalid) == 1  # 负阈值 price 规则被忽略留痕

    def test_duplicate_names_get_migration_suffix(self):
        """重复名字按文件顺序追加"(迁移2)", 并展示迁移提示。"""
        config = {"version": 2, "active_id": "t1", "templates": [
            _legacy_template(id="a", name="同名策略"),
            _legacy_template(id="b", name=" 同名策略 "),
        ]}
        migrated = migrate_config_to_v3(config)["templates"]
        assert migrated[0]["name"] == "同名策略"
        assert migrated[1]["name"] == "同名策略(迁移2)"
        assert _issues_of(migrated[1], "duplicate_name_renamed")

    def test_missing_and_duplicate_ids_stable(self):
        """缺失/重复 ID 生成稳定迁移 ID, 迁移重复执行得到同一 ID。"""
        config = {"version": 2, "active_id": "t1", "templates": [
            _legacy_template(id=""),
            _legacy_template(id="dup"),
            _legacy_template(id="dup"),
        ]}
        first = migrate_config_to_v3(config)
        second = migrate_config_to_v3(config)
        ids = [t["id"] for t in first["templates"]]
        assert ids == ["migrated-1", "dup", "migrated-3"]
        assert first == second
        assert all(_issues_of(t, "id_generated") for t in (first["templates"][0], first["templates"][2]))

    def test_long_name_truncated(self):
        """超 40 字符名称截断并留痕, 输出保持 ≤40。"""
        migrated = migrate_config_to_v3({"version": 2, "active_id": "t1", "templates": [
            _legacy_template(name="长" * 45),
        ]})["templates"][0]
        assert len(migrated["name"]) == 40
        assert _issues_of(migrated, "name_truncated")

    def test_active_id_reset(self):
        """active_id 悬空 → 重置为首个模板并留痕。"""
        migrated = migrate_config_to_v3({"version": 2, "active_id": "ghost", "templates": [
            _legacy_template(id="a"),
        ]})
        assert migrated["active_id"] == "a"
        assert _issues_of(migrated["templates"][0], "active_id_reset")

    def test_default_three_low_migration_keeps_scoring(self):
        """现存三低模板迁移保留原条件与评分(不替换为新建默认值)。"""
        migrated = migrate_config_to_v3(DEFAULT_CONFIG)
        assert migrated["version"] == 3
        tmpl = migrated["templates"][0]
        assert tmpl["id"] == "three_low"
        factors = [f["field"] for f in tmpl["strategy_factors"]]
        assert factors == ["dblow", "premium_rt", "curr_iss_amt"]
        # 7 条排除规则 + 评级 + 强赎 + 安全天数 + ST = 11 条
        assert len(tmpl["conditions"]) == 10  # 旧图标转入待确认记录
        assert _issues_of(tmpl, "redeem_semantics")
        assert _find_condition(tmpl, "pb")["value"] == 1
        rating = _find_condition(tmpl, "rating_cd")
        assert rating["value"] == ["A", "A+", "A-", "AA", "AA+", "AA-", "AAA"]
        # 迁移产物必须能通过 V3 schema(否则 GET→POST 回存会被 422)
        SelectionTemplateModel.model_validate(tmpl)

    def test_migrated_template_passes_schema(self):
        migrated = migrate_config_to_v3(
            {"version": 2, "active_id": "t1", "templates": [_legacy_template()]}
        )["templates"][0]
        model = SelectionTemplateModel.model_validate(migrated)
        assert model.id == "t1"


# ---------------------------------------------------------------------------
# GET /cb-list/factors
# ---------------------------------------------------------------------------
class TestFactorsGetApi:
    def test_no_file_returns_migrated_default_with_missing_revision(
        self, contract_client, tmp_factors
    ):
        r = contract_client.get(f"{BASE}/factors")
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == 3
        assert body["revision"] == "missing"
        assert body["templates"][0]["id"] == "three_low"
        assert isinstance(body["templates"][0]["migration_issues"], list)
        # GET 不落盘(§5.3-1)
        assert not tmp_factors.exists()

    def test_get_migrates_in_memory_without_disk_write(self, contract_client, tmp_factors):
        legacy = {"version": 2, "active_id": "t1",
                  "templates": [_legacy_template()]}
        raw = json.dumps(legacy, ensure_ascii=False).encode("utf-8")
        tmp_factors.write_bytes(raw)
        r = contract_client.get(f"{BASE}/factors")
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == 3
        assert body["templates"][0]["name"] == "旧策略"
        assert "excluded_ratings" not in body["templates"][0]
        # 磁盘逐字节不变, 且没有产生备份
        assert tmp_factors.read_bytes() == raw
        assert not list(tmp_factors.parent.glob("factors.pre-v3.*"))

    def test_get_revision_is_sha_of_disk_bytes(self, contract_client, tmp_factors):
        raw = json.dumps({"version": 2, "active_id": "t1",
                          "templates": [_legacy_template()]}, ensure_ascii=False).encode("utf-8")
        tmp_factors.write_bytes(raw)
        r = contract_client.get(f"{BASE}/factors")
        assert r.json()["revision"] == hashlib.sha256(raw).hexdigest()

    def test_corrupt_json_is_503_not_default(self, contract_client, tmp_factors):
        """坏文件不能回默认模板(§5.3-6): 503 CONFIG_UNREADABLE。"""
        tmp_factors.write_bytes(b"{ this is not json ")
        r = contract_client.get(f"{BASE}/factors")
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "CONFIG_UNREADABLE"

    def test_unsupported_version_is_503(self, contract_client, tmp_factors):
        tmp_factors.write_text(json.dumps(
            {"version": 99, "active_id": "x", "templates": []}), encoding="utf-8")
        r = contract_client.get(f"{BASE}/factors")
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "CONFIG_UNREADABLE"


# ---------------------------------------------------------------------------
# POST /cb-list/factors(V3)
# ---------------------------------------------------------------------------
class TestFactorsPostV3:
    def test_save_v3_on_empty_disk_roundtrip(self, contract_client, tmp_factors):
        r = contract_client.post(f"{BASE}/factors", json=_v3_config())
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        on_disk = tmp_factors.read_bytes()
        assert data["data"]["revision"] == hashlib.sha256(on_disk).hexdigest()
        stored = json.loads(on_disk)
        assert stored["version"] == 3
        assert "revision" not in stored  # revision 不写入配置正文(§5.3-2)
        assert stored["updated_at"]
        # GET 回读一致
        r2 = contract_client.get(f"{BASE}/factors")
        assert r2.json()["revision"] == data["data"]["revision"]
        assert r2.json()["templates"] == stored["templates"]

    def test_two_tabs_same_revision_first_wins_second_conflict(
        self, contract_client, tmp_factors
    ):
        """两个标签拿相同 revision: 第一次保存成功, 第二次 409 不覆盖。"""
        r1 = contract_client.get(f"{BASE}/factors")
        r2 = contract_client.get(f"{BASE}/factors")
        assert r1.json()["revision"] == r2.json()["revision"] == "missing"
        ok = contract_client.post(f"{BASE}/factors", json=_v3_config())
        assert ok.status_code == 200
        conflict = contract_client.post(f"{BASE}/factors", json=_v3_config())
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "CONFIG_CONFLICT"
        # 冲突请求不改磁盘
        after_ok = tmp_factors.read_bytes()
        contract_client.post(f"{BASE}/factors", json=_v3_config())
        assert tmp_factors.read_bytes() == after_ok

    def test_stale_revision_conflict_against_v2_disk(self, contract_client, tmp_factors):
        raw = json.dumps({"version": 2, "active_id": "t1",
                          "templates": [_legacy_template()]}, ensure_ascii=False).encode("utf-8")
        tmp_factors.write_bytes(raw)
        body = _v3_config(revision="stale-revision")
        r = contract_client.post(f"{BASE}/factors", json=body)
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "CONFIG_CONFLICT"
        assert tmp_factors.read_bytes() == raw  # 原文件未被动过

    @pytest.mark.parametrize("bad_name", ["   ", "a" * 41])
    def test_invalid_name_422_and_file_untouched(self, contract_client, tmp_factors, bad_name):
        body = _v3_config(templates=[_v3_template(name=bad_name)])
        r = contract_client.post(f"{BASE}/factors", json=body)
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["code"] == "INVALID_CONFIG"
        assert detail["path"]  # §4.3: 至少包含 code/message/path
        assert not tmp_factors.exists()

    def test_casefold_duplicate_names_422(self, contract_client, tmp_factors):
        body = _v3_config(templates=[
            _v3_template(id="a", name="稳健筛选"),
            _v3_template(id="b", name="稳健筛选".upper() if False else "稳健筛选 "),
        ])
        r = contract_client.post(f"{BASE}/factors", json=body)
        assert r.status_code == 422
        assert not tmp_factors.exists()

    def test_rename_only_changes_name(self, contract_client, tmp_factors):
        assert contract_client.post(f"{BASE}/factors", json=_v3_config()).status_code == 200
        before = json.loads(tmp_factors.read_text(encoding="utf-8"))
        revision = hashlib.sha256(tmp_factors.read_bytes()).hexdigest()
        renamed = _v3_config(
            revision=revision,
            templates=[_v3_template(name="新名字")],
        )
        r = contract_client.post(f"{BASE}/factors", json=renamed)
        assert r.status_code == 200
        after = json.loads(tmp_factors.read_text(encoding="utf-8"))
        assert len(after["templates"]) == 1
        assert after["templates"][0]["name"] == "新名字"
        # 除 name(与存储产物 updated_at)外全部一致
        for key in ("id", "description", "conditions", "strategy_factors",
                    "target_count", "hold_tolerance", "migration_issues"):
            assert after["templates"][0][key] == before["templates"][0][key]
        assert after["active_id"] == before["active_id"] == "stable"

    def test_rename_to_same_name_with_spaces_no_substantive_change(
        self, contract_client, tmp_factors
    ):
        assert contract_client.post(f"{BASE}/factors", json=_v3_config()).status_code == 200
        before = json.loads(tmp_factors.read_text(encoding="utf-8"))
        revision = hashlib.sha256(tmp_factors.read_bytes()).hexdigest()
        body = _v3_config(revision=revision, templates=[_v3_template(name="  稳健筛选  ")])
        r = contract_client.post(f"{BASE}/factors", json=body)
        assert r.status_code == 200
        assert r.json()["data"]["templates"][0]["name"] == "稳健筛选"
        after = json.loads(tmp_factors.read_text(encoding="utf-8"))
        assert after["templates"] == before["templates"]  # 模板内容零变动

    def test_backup_bytes_identical_to_original(self, contract_client, tmp_factors):
        """首次 V3 落盘前独占备份, 且与原始文件逐字节一致(§5.3-4)。"""
        raw = json.dumps({"version": 2, "active_id": "t1",
                          "templates": [_legacy_template()]}, ensure_ascii=False).encode("utf-8")
        tmp_factors.write_bytes(raw)
        revision = hashlib.sha256(raw).hexdigest()
        r = contract_client.post(f"{BASE}/factors", json=_v3_config(revision=revision))
        assert r.status_code == 200
        backups = list(tmp_factors.parent.glob("factors.pre-v3.*.json"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == raw
        # 备份名含旧文件哈希前 12 位
        assert hashlib.sha256(raw).hexdigest()[:12] in backups[0].name
        # 第二次 V3 保存不再新增备份
        revision2 = hashlib.sha256(tmp_factors.read_bytes()).hexdigest()
        r2 = contract_client.post(
            f"{BASE}/factors", json=_v3_config(revision=revision2, active_id="stable"))
        assert r2.status_code == 200
        assert len(list(tmp_factors.parent.glob("factors.pre-v3.*.json"))) == 1

    def test_os_replace_failure_keeps_original_bytes(
        self, contract_client, tmp_factors, monkeypatch
    ):
        """os.replace 失败: 保留原文件字节, 清理本次临时文件(§5.3-5)。"""
        raw = json.dumps({"version": 2, "active_id": "t1",
                          "templates": [_legacy_template()]}, ensure_ascii=False).encode("utf-8")
        tmp_factors.write_bytes(raw)
        revision = hashlib.sha256(raw).hexdigest()

        import os as os_module

        def _boom(src, dst):
            raise OSError("mocked replace failure")

        monkeypatch.setattr(os_module, "replace", _boom)
        r = contract_client.post(
            f"{BASE}/factors", json=_v3_config(revision=revision))
        assert r.status_code == 500  # 服务端异常, 未伪装成功
        assert tmp_factors.read_bytes() == raw  # 原文件逐字节未变
        assert not list(tmp_factors.parent.glob("*.tmp"))  # 临时文件已清理

    def test_v3_without_revision_422(self, contract_client, tmp_factors):
        body = _v3_config()
        del body["revision"]
        r = contract_client.post(f"{BASE}/factors", json=body)
        assert r.status_code == 422
        assert not tmp_factors.exists()

    def test_v3_unknown_top_field_422(self, contract_client):
        body = _v3_config(surprise=1)
        r = contract_client.post(f"{BASE}/factors", json=body)
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["path"] == "surprise"

    def test_v3_duplicate_template_ids_422(self, contract_client):
        body = _v3_config(templates=[_v3_template(), _v3_template(name="另一个")])
        r = contract_client.post(f"{BASE}/factors", json=body)
        assert r.status_code == 422

    def test_v3_active_id_must_exist_422(self, contract_client):
        r = contract_client.post(f"{BASE}/factors", json=_v3_config(active_id="ghost"))
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# V1/V2 旧客户端兼容(§5.3-7)
# ---------------------------------------------------------------------------
class TestLegacyClientCompat:
    def test_legacy_post_on_empty_disk_ok(self, contract_client, tmp_factors):
        legacy = {"active_id": "t1", "templates": [{
            "id": "t1", "name": "测试策略", "ratings": ["AA"],
            "exclusion_rules": [], "strategy_factors": [],
        }]}
        r = contract_client.post(f"{BASE}/factors", json=legacy)
        assert r.status_code == 200
        assert r.json()["data"]["templates"][0]["ratings"] == ["AA"]
        assert tmp_factors.exists()

    def test_legacy_post_on_v2_disk_ok(self, contract_client, tmp_factors):
        tmp_factors.write_text(json.dumps(
            {"version": 2, "active_id": "t1", "templates": [_legacy_template()]},
            ensure_ascii=False), encoding="utf-8")
        legacy = {"version": 2, "active_id": "t1", "templates": [{
            "id": "t1", "name": "测试策略", "ratings": [],
            "exclusion_rules": [], "strategy_factors": [],
        }]}
        r = contract_client.post(f"{BASE}/factors", json=legacy)
        assert r.status_code == 200

    def test_legacy_post_on_v3_disk_conflict_409(self, contract_client, tmp_factors):
        """磁盘升级 V3 后, 缺少 revision 的旧客户端写入 → 409 CLIENT_UPGRADE_REQUIRED。"""
        assert contract_client.post(f"{BASE}/factors", json=_v3_config()).status_code == 200
        legacy = {"active_id": "t1", "templates": [{
            "id": "t1", "name": "旧客户端", "ratings": [],
            "exclusion_rules": [], "strategy_factors": [],
        }]}
        r = contract_client.post(f"{BASE}/factors", json=legacy)
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "CLIENT_UPGRADE_REQUIRED"
        # V3 配置未被旧客户端覆盖
        stored = json.loads(tmp_factors.read_text(encoding="utf-8"))
        assert stored["templates"][0]["name"] == "稳健筛选"

    def test_legacy_post_invalid_ratings_still_422(self, contract_client, tmp_factors):
        legacy = {"active_id": "t1", "templates": [{
            "id": "t1", "name": "测试策略", "ratings": "AAA",
            "exclusion_rules": [], "strategy_factors": [],
        }]}
        r = contract_client.post(f"{BASE}/factors", json=legacy)
        assert r.status_code == 422  # 评级语义反例保持
        assert not tmp_factors.exists()
