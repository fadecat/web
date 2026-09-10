# -*- coding: utf-8 -*-
"""筛选模板配置 V1/V2 → V3 确定性迁移(方案 §5)。

职责边界(§6.1):
- 纯函数: 输入深拷贝, 不读写磁盘, 不触网;
- 输出确定且幂等: 相同输入两次迁移结果逐字段一致, 且
  migrate_config_to_v3(migrate_config_to_v3(x)) == migrate_config_to_v3(x);
- V1 前置归一: ratings 缺失而 excluded_ratings 存在时, 先按旧读取逻辑
  反转为保留语义(DEFAULT_RATINGS 差集), 再做 V2→V3 映射(§5.1);
- 废弃/无法映射项一律进入模板级 migration_issues, 不无声丢弃(§5.1),
  其中"旧引擎会真实执行、但 V3 无法等价表达"的条目标 pending(阻塞执行,
  §5.2), 旧引擎本就不执行的条目仅 archived 留痕。

migration_issues 条目固定携带 id/kind/status/message(+origin/original 等):
- replaced_metric            ytm_rt → simple_maturity_yield_pct 特殊迁移(§5.2),
                             status=pending 阻塞执行 / archived(旧停用条目不阻塞);
- unmappable_rule            未知字段/未知运算符/不合法阈值的旧排除规则(启用时
                             pending——旧引擎会执行它, 丢弃即行为变化, 须人工确认);
- duplicate_scoring_dropped  重复/非法评分字段保留首个, 其余 archived;
- unknown_field_dropped      模板级未知字段(旧引擎不读取, archived 留痕);
- invalid_value_dropped      无效枚举值/负阈值/非法权重/超界参数(旧引擎不生效,
                             archived 留痕);
- id_generated               缺失/重复模板 ID 生成的稳定迁移 ID(archived);
- duplicate_name_renamed     重复名称按文件顺序追加"(迁移N)"(archived);
- name_truncated             超 40 字符名称截断(archived);
- active_id_reset            active_id 缺失/悬空时重置为首个模板(archived)。

V3 输入直接原样深拷贝返回(幂等不动点)。
"""
from __future__ import annotations

import copy
from typing import Any

from backend.services.cb_factors import DEFAULT_RATINGS, FACTOR_CATALOG_BY_FIELD
from backend.services.cb_metrics import finite_number

# 模板名规则(§2.3): trim 后 1~40 Unicode 字符, casefold 判重
TEMPLATE_NAME_MAX_CHARS = 40
_RENAME_MARKER = "(迁移{n})"

# V1/V2 模板已知键(迁移读取; 输出模板只保留 V3 键集)
_LEGACY_TEMPLATE_KEYS = {
    "id", "name", "description", "target_count", "hold_tolerance",
    "exclusion_rules", "strategy_factors", "excluded_redeem_icons",
    "redeem_safe_days", "excluded_bond_codes", "ratings", "excluded_ratings",
    "min_listing_days",
}
# V3 模板键(若 V2 输入意外携带, 迁移输出以重建结果为准, 不视作未知字段)
_SUPERSEDED_V3_KEYS = {"conditions", "migration_issues", "updated_at"}

_REDEEM_ICON_VALUES = ("R", "O", "B", "G")


class _IssueSink:
    """模板级迁移问题收集器: 按 kind 独立计数, 保证 id 稳定且不冲突。"""

    def __init__(self) -> None:
        self.issues: list[dict[str, Any]] = []
        self._counters: dict[str, int] = {}

    def add(self, kind: str, id_prefix: str, **payload: Any) -> dict[str, Any]:
        n = self._counters.get(id_prefix, 0) + 1
        self._counters[id_prefix] = n
        entry: dict[str, Any] = {"id": f"{id_prefix}-{n}", "kind": kind, **payload}
        self.issues.append(entry)
        return entry

    def invalid_count(self) -> int:
        return self._counters.get("invalid-value", 0)


def _normalize_bond_code_6(value: Any) -> str:
    """转债代码归一为 6 位数字码(与条件引擎同口径, 去 .SH/.SZ 后缀)。"""
    text = str(value or "").strip().upper()
    for suffix in (".SH", ".SZ"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def _dedupe_name(name: str, used_casefold: set[str]) -> tuple[str, bool]:
    """重复名字按文件顺序追加"(迁移N)", 始终保持 ≤40 字符(§5.1)。

    返回 (最终名字, 是否改名)。
    """
    if name.casefold() not in used_casefold:
        return name, False
    n = 2
    while True:
        suffix = _RENAME_MARKER.format(n=n)
        candidate = name[: TEMPLATE_NAME_MAX_CHARS - len(suffix)] + suffix
        if candidate.casefold() not in used_casefold:
            return candidate, True
        n += 1


def _gen_id(ordinal: int, used_ids: set[str]) -> str:
    """缺失/重复 ID 的稳定迁移 ID: migrated-<文件序号>, 冲突时追加 -k。"""
    base = f"migrated-{ordinal}"
    candidate, k = base, 1
    while candidate in used_ids:
        k += 1
        candidate = f"{base}-{k}"
    return candidate


def _migrate_exclusion_rules(src: dict[str, Any], conditions: list[dict[str, Any]], sink: _IssueSink) -> None:
    """V2 exclusion_rules → V3 conditions(§5.1 转换表 + 废弃项可见化)。"""
    for rule in src.get("exclusion_rules") or []:
        if not isinstance(rule, dict):
            sink.add("unmappable_rule", "unmappable-rule",
                     origin="condition", original=rule, status="pending",
                     message="旧排除规则结构非法(不是对象), 需人工确认后处理")
            continue
        field = str(rule.get("field") or "").strip()
        enabled = bool(rule.get("enabled", True))
        if field == "ytm_rt":
            # §5.2: 集思录 YTM 条件不能无声平移到简单收益率, 一律确认
            sink.add("replaced_metric", "legacy-yield",
                     origin="condition",
                     original={"field": "ytm_rt", "op": rule.get("op"),
                               "threshold": rule.get("threshold")},
                     replacement_field="simple_maturity_yield_pct",
                     status="pending" if enabled else "archived",
                     message="旧集思录收益率条件需要确认" if enabled
                     else "旧集思录收益率条件原本已停用, 仅保留迁移记录")
            continue
        entry = FACTOR_CATALOG_BY_FIELD.get(field)
        if entry is None or entry["type"] != "number":
            sink.add("unmappable_rule", "unmappable-rule",
                     origin="condition", original=rule,
                     status="pending" if enabled else "archived",
                     message=f"旧排除规则字段 {field or '(空)'} 不在 V3 因子目录,"
                             f" 需人工确认后处理" if enabled
                     else f"旧排除规则字段 {field or '(空)'} 不在 V3 因子目录(原本已停用)")
            continue
        op = rule.get("op")
        if op not in ("lt", "gt"):
            sink.add("unmappable_rule", "unmappable-rule",
                     origin="condition", original=rule,
                     status="pending" if enabled else "archived",
                     message=f"旧排除规则运算符 {op!r} 无法映射(仅支持 lt/gt)")
            continue
        threshold = finite_number(rule.get("threshold"))
        if threshold is None:
            sink.add("unmappable_rule", "unmappable-rule",
                     origin="condition", original=rule,
                     status="pending" if enabled else "archived",
                     message=f"旧排除规则阈值 {rule.get('threshold')!r} 不是有效数字")
            continue
        if threshold < 0 and not entry["allow_negative"]:
            # 旧引擎对不允许负值的字段配负阈值永远不会命中, 属无效规则
            sink.add("invalid_value_dropped", "invalid-value",
                     original=rule, status="archived",
                     message=f"字段 {field} 不允许负阈值, 旧负阈值规则不生效, 已忽略")
            continue
        # 相等边界保留: lt X → gte X, gt X → lte X; 旧引擎缺失放行 → include
        conditions.append({
            "id": f"c{len(conditions) + 1}",
            "field": field,
            "op": "gte" if op == "lt" else "lte",
            "value": threshold,
            "enabled": enabled,
            "missing": "include",
            "negative": "compare",
        })


def _migrate_ratings(src: dict[str, Any]) -> list[str]:
    """V1 ratings 前置归一(§5.1): excluded_ratings 反转, 再统一大写排序。"""
    ratings = src.get("ratings")
    if ratings is None:
        legacy_excluded = src.get("excluded_ratings")
        if legacy_excluded is None:
            return []
        return sorted(
            DEFAULT_RATINGS - {str(x).strip().upper() for x in legacy_excluded}
        )
    return sorted({str(x).strip().upper() for x in ratings if str(x).strip()})


def _migrate_redeem_icons(src: dict[str, Any], conditions: list[dict[str, Any]], sink: _IssueSink) -> None:
    """excluded_redeem_icons → redeem_icons not_any(缺省 R/O/B, 显式[]不限)。"""
    raw_icons = src.get("excluded_redeem_icons")
    if raw_icons is None:
        icon_list = ["R", "O", "B"]
    else:
        icon_list: list[str] = []
        seen: set[str] = set()
        for item in raw_icons:
            token = str(item).strip().upper()
            if not token or token in seen:
                continue
            seen.add(token)
            if token in _REDEEM_ICON_VALUES:
                icon_list.append(token)
            else:
                sink.add("invalid_value_dropped", "invalid-value",
                         original=item, status="archived",
                         message=f"强赎标记 {token!r} 不在 R/O/B/G 内(旧引擎不会命中), 已忽略")
    if icon_list:
        conditions.append({
            "id": f"c{len(conditions) + 1}",
            "field": "redeem_icons", "op": "not_any", "value": icon_list,
            "enabled": True, "missing": "exclude", "negative": "compare",
        })


def _migrate_strategy_factors(src: dict[str, Any], factors_out: list[dict[str, Any]], sink: _IssueSink) -> None:
    """V2 strategy_factors → V3(仅保留 field/ascending/weight/enabled)。"""
    seen_fields: set[str] = set()
    for factor in src.get("strategy_factors") or []:
        if not isinstance(factor, dict):
            sink.add("duplicate_scoring_dropped", "duplicate-scoring",
                     original=factor, status="archived",
                     message="旧评分因子结构非法(不是对象), 已忽略(旧引擎也不生效)")
            continue
        field = str(factor.get("field") or "").strip()
        enabled = bool(factor.get("enabled", True))
        if field == "ytm_rt":
            # §5.2: 评分里的 ytm_rt 同样 pending(启用时); 停用仅留档不阻塞
            sink.add("replaced_metric", "legacy-yield",
                     origin="scoring_factor",
                     original={"field": "ytm_rt",
                               "ascending": bool(factor.get("ascending", True)),
                               "weight": factor.get("weight"),
                               "enabled": enabled},
                     replacement_field="simple_maturity_yield_pct",
                     status="pending" if enabled else "archived",
                     message="旧集思录收益率评分需要确认" if enabled
                     else "旧集思录收益率评分原本已停用, 仅保留迁移记录")
            continue
        entry = FACTOR_CATALOG_BY_FIELD.get(field)
        if entry is None or not entry.get("scorable"):
            sink.add("duplicate_scoring_dropped", "duplicate-scoring",
                     original=factor, status="archived",
                     message=f"评分字段 {field or '(空)'} 未知或不可评分, 已忽略"
                             "(旧引擎对其得 0 分, 不影响总分)")
            continue
        weight = finite_number(factor.get("weight"))
        if weight is None or weight <= 0:
            sink.add("invalid_value_dropped", "invalid-value",
                     original=factor, status="archived",
                     message=f"评分权重 {factor.get('weight')!r} 非正数, 已重置为 1")
            weight = 1.0
        if field in seen_fields:
            sink.add("duplicate_scoring_dropped", "duplicate-scoring",
                     original=factor, status="archived",
                     message=f"字段 {field} 重复评分, 仅保留首个(V3 不允许重复评分)")
            continue
        seen_fields.add(field)
        factors_out.append({
            "field": field,
            "ascending": bool(factor.get("ascending", True)),
            "weight": float(weight),
            "enabled": enabled,
        })


def _migrate_template(
    src: dict[str, Any],
    ordinal: int,
    used_ids: set[str],
    used_names: set[str],
) -> dict[str, Any]:
    """单个 V1/V2 模板 → V3 模板(确定性; issues 与源键顺序无关)。"""
    sink = _IssueSink()

    # --- id: 缺失/重复 → 稳定迁移 ID(§5.1, 重复执行得到同一 ID)
    tid = str(src.get("id") or "").strip()
    if not tid:
        tid = _gen_id(ordinal, used_ids)
        sink.add("id_generated", "id-generated",
                 status="archived", generated_id=tid,
                 message=f"模板缺少 ID, 生成稳定迁移 ID: {tid}")
    elif tid in used_ids:
        new_id = _gen_id(ordinal, used_ids)
        sink.add("id_generated", "id-generated",
                 status="archived", generated_id=new_id,
                 message=f"模板 ID {tid} 重复, 生成稳定迁移 ID: {new_id}")
        tid = new_id
    used_ids.add(tid)

    # --- name: trim → 截断 → 重名追加"(迁移N)"
    original_name = str(src.get("name") or "").strip()
    name = original_name
    if len(name) > TEMPLATE_NAME_MAX_CHARS:
        name = name[:TEMPLATE_NAME_MAX_CHARS]
        sink.add("name_truncated", "name-truncated",
                 status="archived", original=original_name,
                 message=f"模板名超过 {TEMPLATE_NAME_MAX_CHARS} 字符, 已截断")
    name, renamed = _dedupe_name(name, used_names)
    if renamed:
        sink.add("duplicate_name_renamed", "name-renamed",
                 status="archived", original=original_name,
                 message=f"重名模板按文件顺序改名: {original_name} -> {name}")
    used_names.add(name.casefold())

    # --- 未知模板键留痕(§5.1: 不能丢掉后无声)
    unknown_keys = sorted(
        k for k in src
        if k not in _LEGACY_TEMPLATE_KEYS and k not in _SUPERSEDED_V3_KEYS
    )
    if unknown_keys:
        sink.add("unknown_field_dropped", "unknown-field",
                 status="archived", fields=unknown_keys,
                 message=f"模板级未知字段不被 V3 支持, 已忽略: {unknown_keys}")

    # --- 数值兜底: target_count 1~50 / hold_tolerance 0~20(超界夹取并留痕)
    def _clamp_int(raw: Any, default: int, lo: int, hi: int, label: str) -> int:
        value = finite_number(raw)
        if value is None:
            value = float(default)
        clamped = int(max(lo, min(hi, round(value))))
        if clamped != value:
            sink.add("invalid_value_dropped", "invalid-value",
                     status="archived",
                     message=f"{label} {raw!r} 超出 V3 范围[{lo}, {hi}], 已夹取为 {clamped}")
        return clamped

    target_count = _clamp_int(src.get("target_count"), 10, 1, 50, "target_count")
    hold_tolerance = _clamp_int(src.get("hold_tolerance"), 0, 0, 20, "hold_tolerance")

    # --- 迁移映射(§5.1 转换表, 顺序固定保证确定性)
    conditions: list[dict[str, Any]] = []
    _migrate_exclusion_rules(src, conditions, sink)
    ratings_norm = _migrate_ratings(src)
    if ratings_norm:
        # 旧引擎评级白名单连"缺失评级"一起排除 → missing=exclude 保持原语义
        conditions.append({
            "id": f"c{len(conditions) + 1}",
            "field": "rating_cd", "op": "in", "value": ratings_norm,
            "enabled": True, "missing": "exclude", "negative": "compare",
        })
    _migrate_redeem_icons(src, conditions, sink)
    safe_days = finite_number(src.get("redeem_safe_days"))
    if safe_days is not None and safe_days >= 0:
        # 旧逻辑只排除 0≤remain≤阈值: negative=include(负值通过)+missing=include
        conditions.append({
            "id": f"c{len(conditions) + 1}",
            "field": "redeem_remain_days", "op": "gt", "value": int(safe_days),
            "enabled": True, "missing": "include", "negative": "include",
        })
    min_days = finite_number(src.get("min_listing_days"))
    if min_days is not None and min_days > 0:
        conditions.append({
            "id": f"c{len(conditions) + 1}",
            "field": "listed_days", "op": "gte", "value": int(min_days),
            "enabled": True, "missing": "include", "negative": "compare",
        })
    codes: list[str] = []
    seen_codes: set[str] = set()
    for item in src.get("excluded_bond_codes") or []:
        raw = item.get("code") if isinstance(item, dict) else item
        code6 = _normalize_bond_code_6(raw)
        if len(code6) != 6 or not code6.isdigit():
            sink.add("invalid_value_dropped", "invalid-value",
                     original=item, status="archived",
                     message=f"排除代码 {raw!r} 无法归一为 6 位代码, 已忽略(旧引擎也不会命中)")
            continue
        if code6 in seen_codes:
            continue
        seen_codes.add(code6)
        codes.append(code6)
    if codes:
        conditions.append({
            "id": f"c{len(conditions) + 1}",
            "field": "code", "op": "not_in", "value": codes,
            "enabled": True, "missing": "exclude", "negative": "compare",
        })
    # 后端固定 ST 排除 → 显式模板条件, 初始行为不放宽(§5.1)
    conditions.append({
        "id": f"c{len(conditions) + 1}",
        "field": "stock_is_st", "op": "eq", "value": False,
        "enabled": True, "missing": "exclude", "negative": "compare",
    })

    factors_out: list[dict[str, Any]] = []
    _migrate_strategy_factors(src, factors_out, sink)

    return {
        "id": tid,
        "name": name,
        "description": str(src.get("description") or ""),
        "conditions": conditions,
        "strategy_factors": factors_out,
        "target_count": target_count,
        "hold_tolerance": hold_tolerance,
        "migration_issues": sink.issues,
    }


def migrate_config_to_v3(config: dict[str, Any]) -> dict[str, Any]:
    """任意版本模板配置 → V3。深拷贝入参, 确定性、幂等、零磁盘 IO(§5.1)。

    - version=3 输入: 原样深拷贝返回(幂等不动点);
    - V1/V2(或缺 version): 逐模板执行 §5.1 转换表 + §5.2 ytm_rt 特殊迁移;
    - active_id 悬空时重置为首个模板并留痕(§5.1)。
    """
    if not isinstance(config, dict):
        raise TypeError("模板配置必须是 JSON 对象")
    if config.get("version") == 3:
        return copy.deepcopy(config)

    templates_in = config.get("templates") or []
    used_ids: set[str] = set()
    used_names: set[str] = set()
    templates_out = [
        _migrate_template(tmpl, ordinal, used_ids, used_names)
        for ordinal, tmpl in enumerate(templates_in, 1)
    ]

    result: dict[str, Any] = {"version": 3}
    active_id = config.get("active_id")
    ids = [t["id"] for t in templates_out]
    if active_id in ids:
        result["active_id"] = active_id
    elif templates_out:
        result["active_id"] = ids[0]
        templates_out[0]["migration_issues"].append({
            "id": "active-id-reset",
            "kind": "active_id_reset", "status": "archived",
            "message": f"active_id {active_id!r} 缺失或不存在, 已重置为首个模板",
            "original": active_id,
        })
    else:
        result["active_id"] = (
            active_id if isinstance(active_id, str) and active_id.strip() else ""
        )
    if "updated_at" in config:
        result["updated_at"] = config["updated_at"]
    result["templates"] = templates_out
    return result
