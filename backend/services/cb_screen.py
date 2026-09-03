# -*- coding: utf-8 -*-
"""可转债筛选打分引擎。

对齐 v2_cb_rotation 的 jisilu_service.py 核心逻辑:
- check_exclusion_rules: 数值阈值硬过滤(lt/gt)
- get_cb_filter_reasons: 强赎 icons 排除 + ST 排除
- assign_factor_scores: 因子排名打分(线性映射)
- three_low_strategy: 因子得分求和 + 排序
- filter_cb: 组合过滤(强赎/ST/阈值/上市天数/排除代码)

与 v2 的差异:
- 数据源从「集思录实时拉取」改为「数据库 CbDailySnapshot 全量快照」
- 强赎 icons / 正股 ST 从 raw_json 解析(icons、stock_nm 字段已落库)
- 纯函数设计,入参为行数据,出参为可序列化 dict,不碰 DB
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Callable

from backend.services.cb_factors import build_bond_code_match_set

# 强赎图标 → 中文标签
_REDEEM_LABELS = {
    "R": "已公告强赎",
    "O": "公告要强赎",
    "B": "已满足强赎条件",
    "G": "公告不强赎",
}

# 从 DB 行提取字段的映射: 结构化字段直接取,icons 从 raw_json 解析
# 注意: raw_json 里的值可能是字符串,需统一 parse


def _safe_float(value: Any) -> float | None:
    """安全转 float,None/空串/'-' 返回 None。"""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _cell_value(row: Any, field: str) -> Any:
    """从 DB 行取字段值。

    优先取 ORM 属性(已 parse 好的 float),缺失时从 raw_json 兜底。
    """
    if hasattr(row, field):
        return getattr(row, field)
    # 兜底: 从 raw_json 取
    raw = getattr(row, "raw_json", None)
    if raw:
        try:
            cell = json.loads(raw) if isinstance(raw, str) else raw
            return cell.get(field)
        except Exception:
            return None
    return None


def _row_to_cell(row: Any) -> dict[str, Any]:
    """将 DB 行转为 cell dict(兼容 v2 的 row['cell'] 结构)。

    结构化字段直接映射,icons/stock_nm 等未建模字段从 raw_json 补齐。
    """
    cell: dict[str, Any] = {}
    for field in (
        "bond_id", "bond_nm", "stock_id", "stock_nm",
        "price", "sprice", "increase_rt", "sincrease_rt",
        "convert_price", "convert_value", "premium_rt", "dblow",
        "curr_iss_amt", "orig_iss_amt", "year_left",
        "maturity_dt", "list_dt", "rating_cd",
        "ytm_rt", "put_ytm_rt", "pb", "turnover_rt",
        "volume", "svolume", "force_redeem_price",
        "put_convert_price", "convert_amt_ratio",
        "market_cd", "sw_cd", "btype",
    ):
        cell[field] = _cell_value(row, field)

    # 从 raw_json 补齐未单独建模的字段(icons 强赎标记等)
    raw = getattr(row, "raw_json", None)
    if raw:
        try:
            extra = json.loads(raw) if isinstance(raw, str) else raw
            for key, val in extra.items():
                if key not in cell:
                    cell[key] = val
        except Exception:
            pass

    return cell


# ---------------------------------------------------------------------------
# 排除规则
# ---------------------------------------------------------------------------

def check_exclusion_rules(cell: dict[str, Any], rules: list[dict] | None = None) -> list[str]:
    """检查数值排除规则,返回命中的排除原因列表(空列表=通过)。"""
    active_rules = [r for r in (rules or []) if r.get("enabled", True)]
    reasons: list[str] = []
    for rule in active_rules:
        raw = cell.get(rule["field"])
        val = _safe_float(raw)
        if val is None:
            continue  # 取不到值则放行(不误排除)
        try:
            threshold = float(rule["threshold"])
        except (TypeError, ValueError):
            continue
        op = rule.get("op")
        if op == "lt" and val < threshold:
            reasons.append(rule.get("label", rule.get("field", "")))
        elif op == "gt" and val > threshold:
            reasons.append(rule.get("label", rule.get("field", "")))
    return reasons


def get_cb_filter_reasons(cell: dict[str, Any], excluded_redeem_icons: list[str] | None = None) -> list[str]:
    """强赎 icons 排除 + 正股 ST 排除,返回命中原因。"""
    reasons: list[str] = []
    icons = cell.get("icons") or {}
    active_icons = excluded_redeem_icons if excluded_redeem_icons is not None else ["R", "O", "B"]
    for icon in active_icons:
        if icon in icons:
            reasons.append(_REDEEM_LABELS.get(icon, icon))
    if "ST" in (cell.get("stock_nm") or "").upper():
        reasons.append("正股含ST或*ST")
    return reasons


def _get_listed_days(list_dt_str: Any) -> int | None:
    """根据上市日期计算自然日天数。"""
    if not list_dt_str:
        return None
    try:
        listed = datetime.strptime(str(list_dt_str).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    return (date.today() - listed).days


# ---------------------------------------------------------------------------
# 打分
# ---------------------------------------------------------------------------

def assign_factor_scores(rows: list[dict], field: str, ascending: bool = True, weight: float = 1.0) -> None:
    """按 field 排名打分,写入 {field}_score。

    ascending=True → 值越小排名越靠前,得分越高
    排名第 1 得 total×weight,线性递减到第 N 名得 1×weight,无值得 0。
    """
    valid = [
        (i, row, _safe_float(row["cell"].get(field)))
        for i, row in enumerate(rows)
        if _safe_float(row["cell"].get(field)) is not None
    ]
    valid.sort(key=lambda x: (x[2] if ascending else -x[2], x[0]))

    total = len(valid)
    score_key = f"{field}_score"
    for rank, (_, row, _) in enumerate(valid, 1):
        row[score_key] = (total - rank + 1) * weight

    scored_ids = {id(row) for _, row, _ in valid}
    for row in rows:
        if id(row) not in scored_ids:
            row[score_key] = 0.0


def three_low_strategy(rows: list[dict], factors: list[dict] | None = None) -> list[dict]:
    """三低策略: 各因子打分求和,按总分降序 + 双低升序排序。"""
    active_factors = [f for f in (factors or []) if f.get("enabled", True)]
    ranked = list(rows)

    for factor in active_factors:
        assign_factor_scores(
            ranked,
            field=factor["field"],
            ascending=factor.get("ascending", True),
            weight=factor.get("weight", 1.0),
        )

    for row in ranked:
        row["total_score"] = sum(
            row.get(f"{f['field']}_score", 0.0) for f in active_factors
        )

    ranked.sort(
        key=lambda r: (
            -r["total_score"],
            _safe_float(r["cell"].get("dblow")) if _safe_float(r["cell"].get("dblow")) is not None else float("inf"),
        )
    )
    return ranked


# ---------------------------------------------------------------------------
# 过滤组合
# ---------------------------------------------------------------------------

def filter_cb(
    rows: list[dict],
    rules: list[dict] | None = None,
    excluded_redeem_icons: list[str] | None = None,
    redeem_safe_days: int | None = None,
    excluded_bond_codes: list | None = None,
    min_listing_days: int | None = None,
) -> list[dict]:
    """组合过滤: 强赎/ST + 数值阈值 + 排除代码 + 上市天数。

    注意: redeem_safe_days 依赖 redeem_list 的 redeem_remain_days 字段,
    新仓库未抓取 redeem_list,故此项暂不生效(保留接口兼容)。
    """
    safe_days = int(redeem_safe_days) if redeem_safe_days is not None else -1
    try:
        min_days = max(0, int(min_listing_days or 0))
    except (TypeError, ValueError):
        min_days = 0

    excluded_set = build_bond_code_match_set(excluded_bond_codes)

    result: list[dict] = []
    for row in rows:
        c = row["cell"]
        reasons = get_cb_filter_reasons(c, excluded_redeem_icons=excluded_redeem_icons)
        reasons += check_exclusion_rules(c, rules=rules)

        if excluded_set:
            current_set = build_bond_code_match_set([c.get("bond_id")])
            if current_set & excluded_set:
                reasons.append("命中全局排除代码")

        if safe_days >= 0:
            # 新仓库无 redeem_list,强赎临近触发天数判断留待后续补齐
            pass

        if min_days > 0:
            listed_days = _get_listed_days(c.get("list_dt"))
            if listed_days is not None and listed_days < min_days:
                reasons.append(f"上市未满{min_days}天(仅{listed_days}天)")

        if reasons:
            row["_exclude_reasons"] = reasons
            continue

        result.append(row)

    return result


# ---------------------------------------------------------------------------
# 主入口: 打分筛选
# ---------------------------------------------------------------------------

def screen_bonds(
    rows: list[Any],
    template: dict[str, Any],
) -> dict[str, Any]:
    """对 DB 行列表执行完整筛选打分,返回可序列化结果。

    参数:
        rows: CbDailySnapshot ORM 对象列表
        template: 策略模板配置(dict)

    返回:
        {total_all, total_filtered, top_n, keep_n, rows: [...]}
    """
    # 1. DB 行 → cell dict(兼容 v2 结构)
    cell_rows = [{"cell": _row_to_cell(r)} for r in rows]
    total_all = len(cell_rows)

    # 2. 过滤
    filtered = filter_cb(
        cell_rows,
        rules=template.get("exclusion_rules"),
        excluded_redeem_icons=template.get("excluded_redeem_icons"),
        redeem_safe_days=template.get("redeem_safe_days"),
        excluded_bond_codes=template.get("excluded_bond_codes"),
        min_listing_days=template.get("min_listing_days"),
    )

    # 3. 打分排序
    ranked = three_low_strategy(filtered, factors=template.get("strategy_factors"))

    # 4. 取 top_n(target + tolerance)
    target = int(template.get("target_count") or 10)
    tol = max(0, int(template.get("hold_tolerance") or 0))
    keep_n = target + tol
    ranked = ranked[:keep_n]

    # 5. 组装结果
    result_rows: list[dict] = []
    for i, row in enumerate(ranked, 1):
        c = row["cell"]
        result_rows.append({
            "rank": i,
            "selected": i <= target,
            "holdable": i <= keep_n,
            "code": c.get("bond_id", ""),
            "name": c.get("bond_nm", ""),
            "price": _safe_float(c.get("price")),
            "change_rt": _safe_float(c.get("increase_rt")),
            "dblow": _safe_float(c.get("dblow")),
            "premium_rt": _safe_float(c.get("premium_rt")),
            "curr_iss_amt": _safe_float(c.get("curr_iss_amt")),
            "convert_value": _safe_float(c.get("convert_value")),
            "year_left": _safe_float(c.get("year_left")),
            "pb": _safe_float(c.get("pb")),
            "ytm_rt": _safe_float(c.get("ytm_rt")),
            "rating": c.get("rating_cd", ""),
            "redeem": _redeem_status_text(c),
            "total_score": row.get("total_score", 0.0),
        })

    return {
        "total_all": total_all,
        "total_filtered": len(filtered),
        "top_n": target,
        "keep_n": keep_n,
        "rows": result_rows,
    }


def _redeem_status_text(cell: dict[str, Any]) -> str:
    """从 icons 提取强赎状态文本。"""
    icons = cell.get("icons") or {}
    for icon in ("R", "O", "G", "B"):
        if icon in icons:
            return _REDEEM_LABELS.get(icon, icon)
    return ""
