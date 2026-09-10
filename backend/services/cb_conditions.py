# -*- coding: utf-8 -*-
"""V3 条件求值引擎(方案 §4.4, 纯函数)。

职责: 对**已通过 schema 校验**的条件列表逐条求值, 返回结构化失败原因列表;
空列表 = 全部通过。启用条件之间为 AND。

边界(6.1 管线合同):
- 不读数据库、不触网、不按系统当天暗中计算——输入是补充完字段的 cell;
- 不在这里重复旧引擎的 ST/评级硬编码过滤, 只执行模板显式声明的条件;
- 缺失规则: 每条条件 missing=exclude(默认)|include; 空评级/空行业先归一为
  枚举 NONE 再匹配(不是缺失), 其他数值字段取不到值才应用 missing;
- legacy 负值兼容: negative=include 仅用于迁移的 redeem_remain_days 条件,
  表示"负值不参与安全天数判断"(旧引擎只在 0≤remain≤阈值 时排除);
  新建条件用默认 compare, 负值按普通数值参与比较, 不当作缺失。

失败原因结构(§4.4):
    {rule_id, field, actual, op, expected, reason_code, message}
reason_code ∈ missing | value_out_of_range | value_not_in | value_in_excluded
            | value_mismatch
"""
from __future__ import annotations

from typing import Any

from backend.services.cb_factors import FACTOR_CATALOG_BY_FIELD
from backend.services.cb_metrics import finite_number

# 目录字段 → cell 取值键(目录字段名与源数据字段名不同者在此显式映射)
_FIELD_CELL_KEYS: dict[str, str] = {
    "industry_code": "sw_cd",  # 申万原始行业码在 cell 上叫 sw_cd
    "code": "bond_id",         # 转债代码在 cell 上叫 bond_id
}

# 空评级/空行业的枚举占位符(§4.4: 先归一为 NONE 再匹配)
_ENUM_NONE = "NONE"

# 强赎标记合法值域(§4.2)
_REDEEM_ICON_VALUES = {"R", "O", "B", "G"}


def _normalize_bond_code_6(value: Any) -> str:
    """转债代码归一为 6 位数字码(去 .SH/.SZ 交易所后缀)。"""
    text = str(value or "").strip().upper()
    for suffix in (".SH", ".SZ"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def _stock_is_st(cell: dict[str, Any]) -> bool:
    """正股 ST 识别: 复用现有定义(正股名称含 ST/*ST, 大写比较)。"""
    return "ST" in str(cell.get("stock_nm") or "").upper()


def _fail(
    cond: dict[str, Any],
    field: str,
    actual: Any,
    reason_code: str,
    message: str,
) -> dict[str, Any]:
    """构造一条结构化失败原因(§4.4 固定七键)。"""
    return {
        "rule_id": cond.get("id"),
        "field": field,
        "actual": actual,
        "op": cond.get("op"),
        "expected": cond.get("value"),
        "reason_code": reason_code,
        "message": message,
    }


def _missing_message(field: str) -> str:
    """缺失场景的人类可读原因(收益率缺失要说明根因, 不能只写指标名)。"""
    if field == "simple_maturity_yield_pct":
        return "简单到期收益率缺失：无到期赎回价"
    label = FACTOR_CATALOG_BY_FIELD.get(field, {}).get("label", field)
    return f"{label}缺失"


def _eval_numeric(
    cell: dict[str, Any],
    cond: dict[str, Any],
    field: str,
    entry: dict[str, Any],
) -> dict[str, Any] | None:
    """数值条件: gte/lte/gt/between + 缺失规则 + legacy 负值兼容。"""
    actual = finite_number(cell.get(_FIELD_CELL_KEYS.get(field, field)))
    op = cond["op"]
    expected = cond["value"]
    label = entry["label"]

    if actual is None:
        if cond.get("missing", "exclude") == "include":
            return None  # 旧模板兼容: 缺失放行
        return _fail(cond, field, None, "missing", _missing_message(field))

    # legacy: negative=include 表示负值不参与判断(直接通过), 不当缺失
    if cond.get("negative", "compare") == "include" and actual < 0:
        return None

    if op in ("gte", "gt", "lte"):
        threshold = float(expected)
        ok = (
            actual >= threshold if op == "gte"
            else actual > threshold if op == "gt"
            else actual <= threshold
        )
        if ok:
            return None
        return _fail(
            cond, field, actual, "value_out_of_range",
            f"{label}不满足条件(当前 {actual}, 要求 {op} {threshold})",
        )

    # between: 两端都包含
    lo, hi = float(expected[0]), float(expected[1])
    if lo <= actual <= hi:
        return None
    return _fail(
        cond, field, actual, "value_out_of_range",
        f"{label}不在区间内(当前 {actual}, 区间 [{lo}, {hi}])",
    )


def _eval_enum(
    cell: dict[str, Any],
    cond: dict[str, Any],
    field: str,
    entry: dict[str, Any],
) -> dict[str, Any] | None:
    """枚举条件: in 命中任一即通过, not_in 命中任一即排除。

    空评级/空行业先归一为 NONE 再匹配; 评级匹配大小写不敏感(与旧引擎一致),
    行业/代码按原始码精确匹配。
    """
    raw = cell.get(_FIELD_CELL_KEYS.get(field, field))
    if field == "rating_cd":
        actual = str(raw or "").strip().upper() or _ENUM_NONE
    elif field in ("industry_code", "code"):
        if field == "code":
            actual = _normalize_bond_code_6(raw) or _ENUM_NONE
        else:
            actual = str(raw or "").strip() or _ENUM_NONE
    else:  # 预留: 其他枚举字段按字符串归一
        actual = str(raw or "").strip() or _ENUM_NONE

    expected_set = {str(v) for v in cond["value"]}
    op = cond["op"]
    label = entry["label"]

    if op == "in":
        if actual in expected_set:
            return None
        return _fail(
            cond, field, actual, "value_not_in",
            f"{label}不在所选范围(当前 {actual}, 允许 {sorted(expected_set)})",
        )
    # not_in: 命中任一所选值即排除
    if actual not in expected_set:
        return None
    return _fail(
        cond, field, actual, "value_in_excluded",
        f"{label}命中排除项(当前 {actual}, 排除 {sorted(expected_set)})",
    )


def _eval_set(
    cell: dict[str, Any],
    cond: dict[str, Any],
    field: str,
    entry: dict[str, Any],
) -> dict[str, Any] | None:
    """集合条件(not_any): 与所选状态存在交集即排除。

    实际值来自 cell.icons 的键集(强赎标记 R/O/B/G)。
    """
    actual_set = {
        str(k).strip() for k in (cell.get("icons") or {})
        if str(k).strip() in _REDEEM_ICON_VALUES
    }
    expected_set = {str(v) for v in cond["value"]}
    if not (actual_set & expected_set):
        return None
    label = entry["label"]
    return _fail(
        cond, field, sorted(actual_set), "value_in_excluded",
        f"{label}命中排除状态(当前 {sorted(actual_set & expected_set)})",
    )


def _eval_boolean(
    cell: dict[str, Any],
    cond: dict[str, Any],
    field: str,
    entry: dict[str, Any],
) -> dict[str, Any] | None:
    """布尔条件(eq): 实际值由 cell 推导(如 ST 识别)。"""
    actual = _stock_is_st(cell) if field == "stock_is_st" else bool(cell.get(field))
    expected = bool(cond["value"])
    if actual == expected:
        return None
    label = entry["label"]
    return _fail(
        cond, field, actual, "value_mismatch",
        f"{label}不等于要求值(当前 {actual}, 要求 {expected})",
    )


_EVALUATORS = {
    "number": _eval_numeric,
    "enum": _eval_enum,
    "set": _eval_set,
    "boolean": _eval_boolean,
}


def evaluate_conditions(
    cell: dict[str, Any],
    conditions: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """对已校验条件列表求值, 返回失败原因列表(空 = 通过)。

    - 仅执行 enabled=true 的条件; 启用条件间为 AND;
    - 条件必须来自 SelectionRunModel/SelectionTemplateModel 校验后的输出,
      未知字段/运算符在此视为编程错误(直接抛 ValueError, 不静默放行)。
    """
    failures: list[dict[str, Any]] = []
    for cond in conditions or []:
        if not cond.get("enabled", True):
            continue
        field = cond["field"]
        entry = FACTOR_CATALOG_BY_FIELD.get(field)
        if entry is None:
            raise ValueError(f"条件字段未在因子目录中登记: {field}")
        evaluate = _EVALUATORS[entry["type"]]
        reason = evaluate(cell, cond, field, entry)
        if reason is not None:
            failures.append(reason)
    return failures
