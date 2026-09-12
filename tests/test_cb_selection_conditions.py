# -*- coding: utf-8 -*-
"""V3 条件引擎与强校验测试(T2, 方案 §4)。

覆盖:
- evaluate_conditions 纯函数语义: 边界包含、未舍入值比较、缺失规则、
  枚举 NONE 归一、集合交集、布尔、代码归一、AND 组合、legacy 负值兼容;
- V3 schema 强校验(§4.3): 未知字段/非法运算符/bool 冒充数值/NaN/Infinity/
  空枚举集合/重复项/非 scorable 打分/重复打分字段/负数阈值目录标记/
  配置级 id 唯一与 active_id 存在/source 枚举/未知顶层字段;
- 校验先于抓取: 非法执行请求不触发任何数据源调用。
不读库、不触网。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from backend.services.cb_conditions import evaluate_conditions
from backend.services.cb_factors import FACTOR_CATALOG, FACTOR_CATALOG_BY_FIELD
from backend.api.schemas.cb_screen import (
    ConditionModel,
    ScoringFactorModel,
    SelectionConfigModel,
    SelectionRunModel,
    SelectionTemplateModel,
)


# ---------------------------------------------------------------------------
# 因子目录(§4.2 类型化目录)
# ---------------------------------------------------------------------------
class TestCatalog:
    def test_ytm_rt_removed(self):
        assert "ytm_rt" not in FACTOR_CATALOG_BY_FIELD

    def test_new_fields_present(self):
        for field in (
            "simple_maturity_yield_pct", "redeem_price", "industry_code",
            "rating_cd", "redeem_icons", "redeem_remain_days",
            "listed_days", "stock_is_st", "code",
        ):
            assert field in FACTOR_CATALOG_BY_FIELD, field

    def test_every_entry_is_typed(self):
        for entry in FACTOR_CATALOG:
            assert entry["type"] in ("number", "enum", "set", "boolean"), entry
            assert isinstance(entry["operators"], list) and entry["operators"]
            assert isinstance(entry["filterable"], bool)
            assert isinstance(entry["scorable"], bool)
            assert isinstance(entry["allow_negative"], bool)
            assert entry["description"]

    def test_type_specific_operators(self):
        assert FACTOR_CATALOG_BY_FIELD["price"]["operators"] == ["gte", "lte", "gt", "between"]
        assert FACTOR_CATALOG_BY_FIELD["industry_code"]["operators"] == ["in", "not_in"]
        assert FACTOR_CATALOG_BY_FIELD["redeem_icons"]["operators"] == ["not_any"]
        assert FACTOR_CATALOG_BY_FIELD["stock_is_st"]["operators"] == ["eq"]
        assert FACTOR_CATALOG_BY_FIELD["code"]["operators"] == ["not_in"]

    def test_scorable_flags(self):
        assert FACTOR_CATALOG_BY_FIELD["simple_maturity_yield_pct"]["scorable"] is True
        assert FACTOR_CATALOG_BY_FIELD["redeem_price"]["scorable"] is True
        assert FACTOR_CATALOG_BY_FIELD["price"]["scorable"] is True
        for field in ("industry_code", "rating_cd", "redeem_icons",
                      "redeem_remain_days", "listed_days", "stock_is_st", "code"):
            assert FACTOR_CATALOG_BY_FIELD[field]["scorable"] is False, field

    def test_negative_markers(self):
        # 收益率/溢价率/涨跌幅/市净率允许负阈值; 价格/规模/年限等不允许
        for field in ("simple_maturity_yield_pct", "premium_rt", "increase_rt",
                      "sincrease_rt", "pb", "redeem_remain_days"):
            assert FACTOR_CATALOG_BY_FIELD[field]["allow_negative"] is True, field
        for field in ("price", "curr_iss_amt", "year_left", "convert_value",
                      "volume", "sprice", "redeem_price", "dblow", "listed_days"):
            assert FACTOR_CATALOG_BY_FIELD[field]["allow_negative"] is False, field

    def test_price_renamed(self):
        assert FACTOR_CATALOG_BY_FIELD["price"]["label"] == "当前价格"

    def test_legacy_numeric_fields_kept(self):
        for field in ("dblow", "premium_rt", "curr_iss_amt", "convert_value",
                      "year_left", "convert_amt_ratio", "volume", "increase_rt",
                      "pb", "sprice", "sincrease_rt"):
            assert field in FACTOR_CATALOG_BY_FIELD, field


# ---------------------------------------------------------------------------
# 条件模型强校验(§4.3)
# ---------------------------------------------------------------------------
def _cond(**overrides):
    base = {"id": "c1", "field": "price", "op": "between", "value": [80, 120], "enabled": True}
    base.update(overrides)
    return base


class TestConditionModelValidation:
    @pytest.mark.parametrize('payload', [
        _cond(field="nonexistent_field"),            # 未知字段
        _cond(op="equals"),                          # 非法运算符
        _cond(field="industry_code", op="gte", value=10),   # 运算符与类型不匹配
        _cond(value="80"),                           # 字符串冒充数值(strict)
        _cond(value=True),                           # bool 冒充数值
        _cond(value=[80, True]),                     # 区间中 bool
        _cond(value=[120, 80]),                      # 区间 lo > hi
        _cond(value=[80]),                           # 区间长度错误
        _cond(value=float("nan")),                   # NaN
        _cond(value=float("inf")),                   # Infinity
        _cond(enabled="yes"),                        # 开关必须 bool
        _cond(missing="maybe"),                      # missing 枚举外
        _cond(negative="include"),                   # negative 只允许 redeem_remain_days
        _cond(field="simple_maturity_yield_pct", op="gte", value=0, missing="include"),  # 新收益率必须 exclude
        _cond(field="redeem_price", op="gte", value=103, missing="include"),             # 赎回价必须 exclude
        {"id": " ", "field": "price", "op": "lte", "value": 120},  # 空 id
    ])
    def test_invalid_condition_rejected(self, payload):
        with pytest.raises(ValidationError):
            ConditionModel.model_validate(payload)

    def test_unknown_field_rejected_even_when_disabled(self):
        # enabled=false 也要求结构有效(不伪装为合法停用条件)
        with pytest.raises(ValidationError):
            ConditionModel.model_validate(_cond(field="ytm_rt", enabled=False))

    def test_disabled_valid_condition_accepted(self):
        m = ConditionModel.model_validate(_cond(enabled=False))
        assert m.enabled is False

    def test_negative_threshold_by_catalog(self):
        # 收益率允许负阈值
        ok = ConditionModel.model_validate(
            _cond(field="simple_maturity_yield_pct", op="gte", value=-5))
        assert ok.value == -5
        # 价格不允许负阈值
        with pytest.raises(ValidationError):
            ConditionModel.model_validate(_cond(field="price", op="gte", value=-1))

    @pytest.mark.parametrize('value', ["AA", ["AA", " "], ["AA", "AA"], ["AA", " AA "]])
    def test_enum_value_must_be_clean_str_array(self, value):
        with pytest.raises(ValidationError):
            ConditionModel.model_validate(_cond(field="rating_cd", op="in", value=value))

    def test_enum_accepts_known_and_unknown_ratings(self):
        # 未知评级保留, 不裁剪七档; NONE 合法
        m = ConditionModel.model_validate(
            _cond(field="rating_cd", op="in", value=["AA+", "BB+", "NONE"]))
        assert m.value == ["AA+", "BB+", "NONE"]

    def test_industry_code_rejects_chinese_names(self):
        # 行业保存代码不保存名称(§0.3): 中文条目 422
        with pytest.raises(ValidationError):
            ConditionModel.model_validate(_cond(field="industry_code", op="not_in", value=["银行"]))

    def test_industry_code_accepts_code_form_and_none(self):
        m = ConditionModel.model_validate(
            _cond(field="industry_code", op="not_in", value=["760201", "610101", "NONE"]))
        assert m.value == ["760201", "610101", "NONE"]

    def test_enum_empty_array_rejected_when_enabled(self):
        with pytest.raises(ValidationError):
            ConditionModel.model_validate(_cond(field="rating_cd", op="in", value=[]))

    def test_enum_empty_array_allowed_when_disabled(self):
        # 停用条件结构仍需有效, 但空集合在停用下可接受(不参与执行)
        m = ConditionModel.model_validate(
            _cond(field="rating_cd", op="in", value=[], enabled=False))
        assert m.enabled is False

    def test_set_icons_restricted_values(self):
        with pytest.raises(ValidationError):
            ConditionModel.model_validate(_cond(field="redeem_icons", op="not_any", value=["X"]))
        m = ConditionModel.model_validate(
            _cond(field="redeem_icons", op="not_any", value=["R", "O", "B"]))
        assert m.value == ["R", "O", "B"]

    def test_boolean_requires_real_bool(self):
        with pytest.raises(ValidationError):
            ConditionModel.model_validate(_cond(field="stock_is_st", op="eq", value=0))
        m = ConditionModel.model_validate(_cond(field="stock_is_st", op="eq", value=False))
        assert m.value is False

    def test_code_values_normalized_to_6_digits(self):
        m = ConditionModel.model_validate(
            _cond(field="code", op="not_in", value=["110001.SH", "123456"]))
        assert m.value == ["110001", "123456"]
        with pytest.raises(ValidationError):
            ConditionModel.model_validate(_cond(field="code", op="not_in", value=["abc"]))

    def test_legacy_negative_include_only_for_remain_days(self):
        m = ConditionModel.model_validate(_cond(
            field="redeem_remain_days", op="gt", value=2,
            missing="include", negative="include"))
        assert m.negative == "include"


class TestScoringFactorModelValidation:
    def _score(self, **overrides):
        base = {"field": "dblow", "ascending": True, "weight": 1, "enabled": True}
        base.update(overrides)
        return base

    def test_valid(self):
        m = ScoringFactorModel.model_validate(self._score(weight=0.5))
        assert m.weight == pytest.approx(0.5)

    @pytest.mark.parametrize('overrides', [
        {"weight": 0},            # 零权重
        {"weight": -1},           # 负权重
        {"weight": True},         # bool 冒充
        {"weight": float("nan")},
        {"weight": float("inf")},
        {"weight": "1"},          # 字符串 strict 拒绝
        {"field": "industry_code"},   # 非 scorable 字段
        {"field": "nonexistent"},     # 未知字段
        {"ascending": 1},             # 方向必须 bool
        {"enabled": "on"},
    ])
    def test_invalid_rejected(self, overrides):
        with pytest.raises(ValidationError):
            ScoringFactorModel.model_validate(self._score(**overrides))


# ---------------------------------------------------------------------------
# 模板 / 执行请求 / 整份配置
# ---------------------------------------------------------------------------
def _template(**overrides):
    base = {
        "id": "stable", "name": "稳健筛选",
        "conditions": [_cond()],
        "strategy_factors": [{"field": "dblow", "ascending": True, "weight": 1, "enabled": True}],
    }
    base.update(overrides)
    return base


class TestTemplateAndRunModels:
    def test_valid_template_round_trip(self):
        m = SelectionTemplateModel.model_validate(_template())
        dumped = m.model_dump()
        assert dumped["conditions"][0]["missing"] == "exclude"  # 新条件默认 exclude
        # 再校验幂等
        SelectionTemplateModel.model_validate(dumped)

    def test_retired_count_fields_rejected(self):
        """目标/容差已下线: V3 保存/执行请求携带即 422(extra=forbid)。"""
        with pytest.raises(ValidationError):
            SelectionTemplateModel.model_validate(_template(target_count=10))
        with pytest.raises(ValidationError):
            SelectionTemplateModel.model_validate(_template(hold_tolerance=0))
        with pytest.raises(ValidationError):
            SelectionRunModel.model_validate(
                dict(_template(), schema_version=3, target_count=5)
            )

    def test_unknown_template_field_rejected(self):
        with pytest.raises(ValidationError):
            SelectionTemplateModel.model_validate(_template(exclusion_rules=[]))

    def test_duplicate_scoring_field_rejected(self):
        dup = [{"field": "dblow", "weight": 1}, {"field": "dblow", "weight": 2}]
        with pytest.raises(ValidationError):
            SelectionTemplateModel.model_validate(_template(strategy_factors=dup))

    def test_run_model_requires_schema_version_3(self):
        payload = dict(_template(), schema_version=3, source="db")
        SelectionRunModel.model_validate(payload)
        with pytest.raises(ValidationError):
            SelectionRunModel.model_validate(dict(_template(), schema_version=2, source="db"))
        with pytest.raises(ValidationError):
            SelectionRunModel.model_validate(dict(_template(), schema_version="3", source="db"))

    def test_run_source_only_db_live(self):
        for bad in ("jisilu", "DB ", "", None, 1):
            payload = dict(_template(), schema_version=3, source=bad)
            with pytest.raises(ValidationError):
                SelectionRunModel.model_validate(payload)

    def test_run_model_unknown_top_field_rejected(self):
        payload = dict(_template(), schema_version=3, source="db", whatever=1)
        with pytest.raises(ValidationError):
            SelectionRunModel.model_validate(payload)


class TestConfigModel:
    def _config(self, **overrides):
        base = {
            "version": 3, "revision": "abc123", "active_id": "stable",
            "templates": [_template()],
        }
        base.update(overrides)
        return base

    def test_valid_config(self):
        m = SelectionConfigModel.model_validate(self._config())
        assert m.templates[0].id == "stable"

    def test_duplicate_template_ids_rejected(self):
        with pytest.raises(ValidationError):
            SelectionConfigModel.model_validate(
                self._config(templates=[_template(), _template(id="stable")]))

    def test_active_id_must_exist(self):
        with pytest.raises(ValidationError):
            SelectionConfigModel.model_validate(self._config(active_id="ghost"))

    def test_version_must_be_3(self):
        with pytest.raises(ValidationError):
            SelectionConfigModel.model_validate(self._config(version=2))

    def test_revision_required(self):
        with pytest.raises(ValidationError):
            SelectionConfigModel.model_validate(self._config(revision=""))

    def test_unknown_top_level_field_rejected(self):
        with pytest.raises(ValidationError):
            SelectionConfigModel.model_validate(self._config(surprise=1))

    def test_storage_artifact_updated_at_tolerated(self):
        # GET 回读带 updated_at(存储产物, 非业务字段): 允许透传, 不破坏回存
        m = SelectionConfigModel.model_validate(self._config(updated_at="2026-09-10T10:00:00"))
        assert m.templates[0].id == "stable"


# ---------------------------------------------------------------------------
# evaluate_conditions(§4.4 缺失规则与匹配语义)
# ---------------------------------------------------------------------------
def _cell(**overrides):
    base = {
        "bond_id": "110001", "bond_nm": "测试债", "price": 100,
        "rating_cd": "AA+", "sw_cd": "760201", "icons": {},
        "stock_nm": "正常股份",
    }
    base.update(overrides)
    return base


class TestEvaluateConditions:
    def test_between_is_inclusive(self):
        cond = ConditionModel.model_validate(_cond()).model_dump()
        assert evaluate_conditions(_cell(price=80), [cond]) == []
        assert evaluate_conditions(_cell(price=120), [cond]) == []
        assert evaluate_conditions(_cell(price=79.99), [cond])
        assert evaluate_conditions(_cell(price=120.01), [cond])

    def test_yield_condition_uses_unrounded_value(self):
        # 0.004 显示为 0.00%, 但比较必须用未舍入值: gte 0.005 失败
        cond = ConditionModel.model_validate(_cond(
            field="simple_maturity_yield_pct", op="gte", value=0.005)).model_dump()
        reasons = evaluate_conditions(_cell(simple_maturity_yield_pct=0.004), [cond])
        assert reasons and reasons[0]["actual"] == pytest.approx(0.004)

    def test_missing_new_yield_excluded(self):
        cond = ConditionModel.model_validate(_cond(
            field="simple_maturity_yield_pct", op="gte", value=0)).model_dump()
        reasons = evaluate_conditions(_cell(simple_maturity_yield_pct=None), [cond])
        assert reasons[0]["reason_code"] == "missing"
        assert "无到期赎回价" in reasons[0]["message"]
        assert reasons[0]["rule_id"] == "c1"

    def test_legacy_missing_include(self):
        # 旧模板迁移: missing=include 时缺失放行(兼容旧"取不到值则放行")
        cond = ConditionModel.model_validate(_cond(
            field="price", op="gte", value=90, missing="include")).model_dump()
        assert evaluate_conditions(_cell(price=None), [cond]) == []

    def test_enum_codes_not_names(self):
        cond = ConditionModel.model_validate(_cond(
            field="industry_code", op="in", value=["760201"])).model_dump()
        assert evaluate_conditions(_cell(sw_cd="760201"), [cond]) == []
        reasons = evaluate_conditions(_cell(sw_cd="610101"), [cond])
        assert reasons[0]["actual"] == "610101"

    def test_no_rating_filter_is_unrestricted(self):
        cond = ConditionModel.model_validate(_cond()).model_dump()  # 只有价格条件
        for rating in ("AAA", "BB+", "", None):
            assert evaluate_conditions(_cell(rating_cd=rating), [cond]) == []

    def test_multi_conditions_and_points_at_failed_rule(self):
        price_ok = ConditionModel.model_validate(_cond(id="p1")).model_dump()
        rating_bad = ConditionModel.model_validate(_cond(
            id="r1", field="rating_cd", op="in", value=["AA"])).model_dump()
        reasons = evaluate_conditions(_cell(rating_cd="AA+"), [price_ok, rating_bad])
        assert len(reasons) == 1
        assert reasons[0]["rule_id"] == "r1"
        assert reasons[0]["field"] == "rating_cd"

    def test_disabled_conditions_skipped(self):
        cond = ConditionModel.model_validate(_cond(enabled=False)).model_dump()
        assert evaluate_conditions(_cell(price=999), [cond]) == []

    def test_redeem_icons_not_any(self):
        cond = ConditionModel.model_validate(_cond(
            field="redeem_icons", op="not_any", value=["R", "O", "B"])).model_dump()
        assert evaluate_conditions(_cell(icons={"R": "1"}), [cond])
        assert evaluate_conditions(_cell(icons={}), [cond]) == []
        assert evaluate_conditions(_cell(icons={"G": "1"}), [cond]) == []

    def test_stock_is_st_uses_current_definition(self):
        cond = ConditionModel.model_validate(_cond(
            field="stock_is_st", op="eq", value=False)).model_dump()
        assert evaluate_conditions(_cell(stock_nm="某ST股份"), [cond])
        assert evaluate_conditions(_cell(stock_nm="*ST某股"), [cond])
        assert evaluate_conditions(_cell(stock_nm="正常股份"), [cond]) == []

    def test_code_not_in_normalizes_exchange_suffix(self):
        cond = ConditionModel.model_validate(_cond(
            field="code", op="not_in", value=["110001"])).model_dump()
        assert evaluate_conditions(_cell(bond_id="110001.SH"), [cond])
        assert evaluate_conditions(_cell(bond_id="110002"), [cond]) == []

    def test_empty_rating_and_industry_normalized_to_none(self):
        allow_none = ConditionModel.model_validate(_cond(
            id="n1", field="rating_cd", op="in", value=["NONE"])).model_dump()
        assert evaluate_conditions(_cell(rating_cd=""), [allow_none]) == []
        assert evaluate_conditions(_cell(rating_cd=None), [allow_none]) == []
        industry_none = ConditionModel.model_validate(_cond(
            id="n2", field="industry_code", op="in", value=["NONE"])).model_dump()
        assert evaluate_conditions(_cell(sw_cd=""), [industry_none]) == []
        assert evaluate_conditions(_cell(sw_cd=None), [industry_none]) == []
        # 非空未知原始码不是 NONE: 按原始码匹配
        reasons = evaluate_conditions(_cell(sw_cd="999999"), [industry_none])
        assert reasons[0]["actual"] == "999999"

    def test_legacy_negative_include_passes_negative_remain_days(self):
        # 迁移的旧安全天数条件: 负值不参与判断(通过), 0~阈值仍排除
        cond = ConditionModel.model_validate(_cond(
            id="legacy-safe", field="redeem_remain_days", op="gt", value=2,
            missing="include", negative="include")).model_dump()
        assert evaluate_conditions(_cell(redeem_remain_days=-1), [cond]) == []
        assert evaluate_conditions(_cell(redeem_remain_days=None), [cond]) == []
        assert evaluate_conditions(_cell(redeem_remain_days=3), [cond]) == []
        assert evaluate_conditions(_cell(redeem_remain_days=0), [cond])
        assert evaluate_conditions(_cell(redeem_remain_days=2), [cond])

    def test_new_remain_days_condition_compares_negatives(self):
        # 新建条件用 compare: -1 是真实值, 不当缺失, gte 0 失败
        cond = ConditionModel.model_validate(_cond(
            field="redeem_remain_days", op="gte", value=0)).model_dump()
        assert evaluate_conditions(_cell(redeem_remain_days=-1), [cond])
        assert evaluate_conditions(_cell(redeem_remain_days=1), [cond]) == []

    def test_listed_days_condition(self):
        cond = ConditionModel.model_validate(_cond(
            field="listed_days", op="gte", value=30, missing="include")).model_dump()
        assert evaluate_conditions(_cell(listed_days=30), [cond]) == []
        assert evaluate_conditions(_cell(listed_days=29), [cond])
        assert evaluate_conditions(_cell(listed_days=None), [cond]) == []

    def test_numeric_single_ops_boundaries(self):
        gte = ConditionModel.model_validate(_cond(
            id="g", field="price", op="gte", value=100)).model_dump()
        assert evaluate_conditions(_cell(price=100), [gte]) == []
        assert evaluate_conditions(_cell(price=99.99), [gte])
        gt = ConditionModel.model_validate(_cond(
            id="g2", field="price", op="gt", value=100)).model_dump()
        assert evaluate_conditions(_cell(price=100), [gt])
        lte = ConditionModel.model_validate(_cond(
            id="g3", field="price", op="lte", value=100)).model_dump()
        assert evaluate_conditions(_cell(price=100), [lte]) == []
        assert evaluate_conditions(_cell(price=100.01), [lte])

    def test_reason_structure_complete(self):
        cond = ConditionModel.model_validate(_cond(
            id="r9", field="rating_cd", op="in", value=["AA"])).model_dump()
        reasons = evaluate_conditions(_cell(rating_cd="AA+"), [cond])
        r = reasons[0]
        assert set(r) == {"rule_id", "field", "actual", "op", "expected", "reason_code", "message"}
        assert r["message"]


# ---------------------------------------------------------------------------
# 校验先于抓取(§4.3: 执行前先校验再抓取)
# ---------------------------------------------------------------------------
class TestInvalidRulePreventsFetch:
    def test_invalid_run_payload_never_touches_data_source(self):
        """未知字段/NaN/weight0 → ValidationError 且抓取调用 0 次。

        HTTP 路由在 T4 接线; 本测试锁定引擎层合同: 校验失败发生在任何
        数据源调用之前(patch 全量抓取入口证明)。
        """
        with patch("backend.services.queries.live.fetch_live_snapshot") as fetch_mock:
            bad_payloads = [
                dict(_template(), schema_version=3, source="db",
                     conditions=[_cond(field="nonexistent_field")]),      # 未知字段
                dict(_template(), schema_version=3, source="db",
                     conditions=[_cond(value=float("nan"))]),             # NaN
                dict(_template(), schema_version=3, source="db",
                     strategy_factors=[{"field": "dblow", "weight": 0}]),  # weight 0
                dict(_template(), schema_version=3, source="jisilu"),     # source 拼写错误
            ]
            for payload in bad_payloads:
                with pytest.raises(ValidationError):
                    SelectionRunModel.model_validate(payload)
            assert fetch_mock.call_count == 0
