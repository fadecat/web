# -*- coding: utf-8 -*-
"""可转债筛选统一执行引擎(V3, 方案 §6.1)。

管线固定: 输入规范化(_row_to_cell) → 字段补充(enrich: 赎回价/简单到期
收益率/强赎剩余天数/上市天数) → 全局黑名单及模板条件(evaluate_conditions)
→ 评分(排名线性映射) → 入选标记 → DTO。

边界合同(§6.1):
- 纯函数: 不读数据库、不联网、不按系统当天暗中计算上市时间——上市天数以
  keyword-only as_of_date 为基准, 由调用方显式传入(DB 路径=快照交易日 D,
  live 路径=调用时刻的东八区日期);
- 不在新路径执行旧硬编码条件: ST/评级/安全天数等全部来自模板显式条件;
- 沿用函数名 screen_bonds / screen_bonds_live, 新增可选 keyword-only
  as_of_date / blacklist_ids; 业务输入相同则结果相同;
- 黑名单在所有执行入口生效, 作为排除原因返回(不是从最终 rows 静默删除),
  blacklisted_count 与其他排除原因可重叠, 不再从 total 中扣除(§6.2);
- 赎回数据不可用(§3.2 严格同日策略): 赎回相关字段按缺失处理; 模板依赖
  收益率/赎回价(条件或评分)且全市场输入无效时抛
  RedeemDataUnavailableError(路由映射 503), 不返回假正常的空结果;
  与赎回无关的模板继续执行, 只带质量警告。

DB 与 live 共用 _run_pipeline, 仅数据来源与 as_of_date 语义不同。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from backend.services.cb_conditions import evaluate_conditions
from backend.services.cb_metrics import enrich_cell, finite_number
from backend.services.industry import industry_info_of
from backend.services.cb_redeem_semantics import normalize_redeem_state

# 强赎图标 → 中文标签
_REDEEM_LABELS = {
    "R": "已公告强赎",
    "O": "公告要强赎",
    "B": "已满足强赎条件",
    "G": "公告不强赎",
}


class RedeemDataUnavailableError(Exception):
    """全市场赎回/收益率输入无效, 且模板依赖该数据(§3.2, HTTP 503)。"""


# 赎回数据依赖字段(§3.2: 收益率/赎回价条件或评分; redeem_remain_days 的
# 迁移条件为 missing=include, 缺数据时优雅降级, 不算硬依赖)
_REDEEM_DEPENDENT_FIELDS = {"simple_maturity_yield_pct", "redeem_price"}


def _safe_float(value: Any) -> float | None:
    """安全转 float,None/空串/'-' 返回 None(保留旧入口兼容)。"""
    return finite_number(value)


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
    """将 DB 行转为 cell dict(输入规范化, 管线第 1 步)。

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


def _normalize_bond_code_6(value: Any) -> str:
    """转债代码归一为 6 位数字码(与条件引擎同口径, 去 .SH/.SZ 后缀)。"""
    text = str(value or "").strip().upper()
    for suffix in (".SH", ".SZ"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def _listed_days(list_dt: Any, as_of_date: date | None) -> int | None:
    """上市至 as_of_date 的自然日天数; 缺日期/解析失败返回 None。

    不使用系统当天: as_of_date 由调用方显式传入(§6.1)。
    """
    if not list_dt or as_of_date is None:
        return None
    text = str(list_dt).strip()[:10]
    try:
        listed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (as_of_date - listed).days


# ---------------------------------------------------------------------------
# 字段补充(管线第 2 步)
# ---------------------------------------------------------------------------

def _enrich_rows(
    rows: list[Any],
    redeem_map: dict[str, dict[str, Any]],
    as_of_date: date | None,
) -> list[dict[str, Any]]:
    """行数据 → [{cell: 补充后的副本}, ...]。

    补充字段: redeem_price / simple_maturity_yield_pct(公共公式),
    redeem_remain_days(仅取同日强赎快照, 覆盖 raw_json 残留旧值),
    listed_days(以 as_of_date 为基准)。不原地污染输入行。
    """
    cell_rows: list[dict[str, Any]] = []
    for row in rows:
        # live 记录已是 cell dict(取归一副本), DB 行走 ORM 规范化
        if isinstance(row, dict):
            cell = dict(row)
        else:
            cell = _row_to_cell(row)
        redeem_cell = redeem_map.get(_normalize_bond_code_6(cell.get("bond_id"))) \
            or redeem_map.get(str(cell.get("bond_id") or "").strip())
        enriched = enrich_cell(cell, redeem_cell)
        enriched["redeem_state"] = normalize_redeem_state(redeem_cell)
        enriched["redeem_status_code"] = enriched["redeem_state"]["status_code"]
        enriched["redeem_remain_days"] = finite_number(
            (redeem_cell or {}).get("redeem_remain_days")
        )
        enriched["listed_days"] = _listed_days(enriched.get("list_dt"), as_of_date)
        cell_rows.append({"cell": enriched, "_redeem_cell": redeem_cell})
    return cell_rows


# ---------------------------------------------------------------------------
# 全局黑名单 + 模板条件(管线第 3 步)
# ---------------------------------------------------------------------------

def _blacklist_reason(cell: dict[str, Any]) -> dict[str, Any]:
    """黑名单排除原因(与条件引擎相同的七键结构, §6.3)。"""
    return {
        "rule_id": "global_blacklist",
        "field": "code",
        "actual": _normalize_bond_code_6(cell.get("bond_id")) or str(cell.get("bond_id") or ""),
        "op": "not_in",
        "expected": None,
        "reason_code": "blacklisted",
        "message": "命中全局黑名单",
    }


def _apply_filters(
    cell_rows: list[dict[str, Any]],
    conditions: list[dict[str, Any]] | None,
    blacklist_ids: list[str] | set[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """黑名单 + 模板条件。返回 (通过, 被排除, 黑名单命中数)。

    每行只进一个列表; 命中黑名单的行同时满足模板排除条件时原因合并
    (所有排除债只计一次, 原因可多个, §6.2)。
    """
    blacklist = {_normalize_bond_code_6(b) for b in (blacklist_ids or [])}
    blacklist.discard("")
    filtered: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    blacklisted_count = 0
    for row in cell_rows:
        cell = row["cell"]
        reasons: list[dict[str, Any]] = []
        if blacklist and _normalize_bond_code_6(cell.get("bond_id")) in blacklist:
            reasons.append(_blacklist_reason(cell))
        reasons.extend(evaluate_conditions(cell, conditions))
        if reasons:
            row["_exclude_reasons"] = reasons
            excluded.append(row)
            if any(r.get("rule_id") == "global_blacklist" for r in reasons):
                blacklisted_count += 1
        else:
            filtered.append(row)
    return filtered, excluded, blacklisted_count


# ---------------------------------------------------------------------------
# 评分与入选标记(管线第 4/5 步)
# ---------------------------------------------------------------------------

def _template_depends_on_redeem(
    conditions: list[dict[str, Any]] | None,
    factors: list[dict[str, Any]] | None,
) -> bool:
    """模板是否依赖赎回数据(§3.2): 收益率/赎回价的启用条件或评分。"""
    for cond in conditions or []:
        if cond.get("enabled", True) and cond.get("field") in _REDEEM_DEPENDENT_FIELDS:
            return True
    for factor in factors or []:
        if factor.get("enabled", True) and factor.get("field") in _REDEEM_DEPENDENT_FIELDS:
            return True
    return False


def _assign_factor_score(
    rows: list[dict[str, Any]], field: str, ascending: bool, weight: float
) -> None:
    """按 field 排名打分, 写入 {field}_score(确定性: 同分按代码升序)。

    排名第 1 得 total×weight, 线性递减到第 N 名得 1×weight, 无值得 0。
    """
    valid: list[tuple[str, float, dict[str, Any]]] = []
    for row in rows:
        value = finite_number(row["cell"].get(field))
        if value is not None:
            valid.append((str(row["cell"].get("bond_id") or ""), value, row))
    valid.sort(key=lambda t: (t[1] if ascending else -t[1], t[0]))

    total = len(valid)
    score_key = f"{field}_score"
    scored_ids = set()
    for rank, (_, _, row) in enumerate(valid, 1):
        row[score_key] = (total - rank + 1) * weight
        scored_ids.add(id(row))
    for row in rows:
        if id(row) not in scored_ids:
            row[score_key] = 0.0


def _score_and_order(
    filtered: list[dict[str, Any]], factors: list[dict[str, Any]] | None
) -> bool:
    """评分 + 排序(原地)。返回是否为评分模式(有启用的评分因子)。

    评分模式: 总分降序 → 双低升序(None 最后) → 代码升序(稳定性 tiebreak)。
    filter_only: 双低升序(None 最后) → 代码升序。
    """
    enabled = [f for f in (factors or []) if f.get("enabled", True)]
    for factor in enabled:
        _assign_factor_score(
            filtered,
            field=factor["field"],
            ascending=bool(factor.get("ascending", True)),
            weight=float(factor.get("weight") or 1.0),
        )
    if enabled:
        for row in filtered:
            row["total_score"] = sum(
                row.get(f"{f['field']}_score", 0.0) for f in enabled
            )

    def _dblow_key(row: dict[str, Any]) -> float:
        v = finite_number(row["cell"].get("dblow"))
        return v if v is not None else float("inf")

    def _code_key(row: dict[str, Any]) -> str:
        return str(row["cell"].get("bond_id") or "")

    if enabled:
        filtered.sort(key=lambda r: (-r["total_score"], _dblow_key(r), _code_key(r)))
    else:
        filtered.sort(key=lambda r: (_dblow_key(r), _code_key(r)))
    return bool(enabled)


# ---------------------------------------------------------------------------
# DTO(管线第 6 步)
# ---------------------------------------------------------------------------

def _to_dto(
    row: dict[str, Any],
    rank: int | None,
    *,
    selected: bool,
    holdable: bool,
    scored_mode: bool,
) -> dict[str, Any]:
    """cell → 结果 DTO。行业字段按 §3.3: 原始码始终携带, 名称/层级来自映射。"""
    c = row["cell"]
    price = finite_number(c.get("price"))
    redeem_price = finite_number(c.get("redeem_price"))
    info = industry_info_of(c.get("sw_cd")) or {}
    industry_code = str(c.get("sw_cd") or "").strip() or None
    return {
        "rank": rank,
        "selected": selected,
        "holdable": holdable,
        "code": c.get("bond_id", ""),
        "name": c.get("bond_nm", ""),
        "industry_code": industry_code,
        "industry_name": info.get("industry_name"),
        "industry_level": info.get("industry_level"),
        "industry_mapped_code": info.get("industry_mapped_code"),
        "industry_is_fallback": bool(info.get("industry_is_fallback", False)),
        "price": price,
        "change_rt": finite_number(c.get("increase_rt")),
        "dblow": finite_number(c.get("dblow")),
        "premium_rt": finite_number(c.get("premium_rt")),
        "curr_iss_amt": finite_number(c.get("curr_iss_amt")),
        "convert_value": finite_number(c.get("convert_value")),
        "year_left": finite_number(c.get("year_left")),
        "pb": finite_number(c.get("pb")),
        "rating": c.get("rating_cd", ""),
        "redeem_price": redeem_price,
        "simple_maturity_yield_pct": finite_number(c.get("simple_maturity_yield_pct")),
        # 保本价差 = 到期赎回价 - 现价, 正数越大保本垫越厚(负数=现价已高于赎回价)
        "redeem_gap": (
            round(redeem_price - price, 3)
            if redeem_price is not None and price is not None
            else None
        ),
        "redeem": format_redeem_status(c, row.get("_redeem_cell")),
        "total_score": row.get("total_score") if scored_mode else None,
    }


# ---------------------------------------------------------------------------
# 统一管线
# ---------------------------------------------------------------------------

def _run_pipeline(
    cell_rows: list[dict[str, Any]],
    template: dict[str, Any],
    *,
    blacklist_ids: list[str] | set[str] | None,
    trade_date: str | None,
    redeem_trade_date: str | None,
    redeem_loaded: bool,
    redeem_unusable: bool,
    base_warnings: list[str] | None,
) -> dict[str, Any]:
    """统一筛选打分管线上半段之后的全部步骤, 返回可序列化结果(§6.2)。"""
    warnings = list(base_warnings or [])
    conditions = template.get("conditions") or []
    factors = template.get("strategy_factors") or []
    target = max(1, min(50, int(template.get("target_count") or 10)))
    tol = max(0, min(20, int(template.get("hold_tolerance") or 0)))
    keep_n = target + tol
    total_all = len(cell_rows)

    # 收益率缺失计数(全量输入, 单债缺失是质量问题不是失败, §3.2)
    missing_yield_count = sum(
        1 for r in cell_rows if r["cell"].get("simple_maturity_yield_pct") is None
    )
    if redeem_unusable:
        warnings.append("赎回数据不可用: 赎回价/简单到期收益率/强赎剩余天数按缺失处理")
    elif missing_yield_count:
        warnings.append(
            f"{missing_yield_count} 只转债缺少到期赎回价, 简单到期收益率为空"
        )

    # 模板依赖收益率/赎回价且全市场输入无效 → 明确失败, 不返回假空结果(§3.2)
    if (
        total_all
        and missing_yield_count == total_all
        and _template_depends_on_redeem(conditions, factors)
    ):
        raise RedeemDataUnavailableError(
            "全市场赎回数据不可用, 模板依赖简单到期收益率/赎回价, 无法执行;"
            " 请检查强赎快照或改用实时行情"
        )

    filtered, excluded_rows, blacklisted_count = _apply_filters(
        cell_rows, conditions, blacklist_ids
    )
    scored_mode = _score_and_order(filtered, factors)

    rows_out: list[dict[str, Any]] = []
    for i, row in enumerate(filtered, 1):
        rows_out.append(_to_dto(
            row, i,
            selected=bool(scored_mode and i <= target),
            holdable=bool(scored_mode and i <= keep_n),
            scored_mode=scored_mode,
        ))
    excluded_out = [
        _to_dto(row, None, selected=False, holdable=False, scored_mode=False)
        | {"exclude_reasons": row.get("_exclude_reasons", [])}
        for row in excluded_rows
    ]

    selected_count = sum(1 for r in rows_out if r["selected"])
    buffer_count = sum(1 for r in rows_out if r["holdable"] and not r["selected"])

    return {
        "total_all": total_all,
        "total_filtered": len(filtered),
        "total_excluded": len(excluded_out),
        "top_n": target if scored_mode else 0,
        "keep_n": keep_n if scored_mode else 0,
        "selected_count": selected_count,
        "buffer_count": buffer_count,
        "selection_mode": "scored" if scored_mode else "filter_only",
        "rows": rows_out,
        "excluded_rows": excluded_out,
        "meta": {
            "data_status": "ready",
            "trade_date": trade_date,
            "redeem_trade_date": redeem_trade_date,
            "fetched_at": None,
            "quote_time": None,
            "redeem_loaded": redeem_loaded,
            "missing_yield_count": missing_yield_count,
            "blacklisted_count": blacklisted_count,
            "warnings": warnings,
        },
    }


# ---------------------------------------------------------------------------
# 主入口(沿用函数名, 新增 keyword-only as_of_date / blacklist_ids)
# ---------------------------------------------------------------------------

def screen_bonds(
    rows: list[Any],
    template: dict[str, Any],
    redeem_map: dict[str, dict[str, Any]] | None = None,
    *,
    as_of_date: date | None = None,
    blacklist_ids: list[str] | set[str] | None = None,
    trade_date: str | None = None,
    redeem_trade_date: str | None = None,
    redeem_loaded: bool = True,
    redeem_unusable: bool = False,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """对 DB 行列表执行完整筛选打分,返回可序列化结果。

    参数:
        rows: CbDailySnapshot ORM 对象列表
        template: V3 模板配置(dict, 已通过 SelectionRunModel 校验)
        redeem_map: {bond_id: redeem_cell}, 来自 CbRedeemDaily 同日快照
        as_of_date: 上市天数计算的基准日(DB 路径=快照交易日 D)
        blacklist_ids: 全局黑名单代码集合(§6.3)
        trade_date / redeem_trade_date: 报告进 meta 的快照日期(ISO 字符串)
        redeem_loaded: 强赎快照是否可用(严格同日策略下 R≠D 记 False)
        redeem_unusable: 本轮赎回字段是否一律按缺失处理(R 不存在或 R≠D)
        warnings: 调用方注入的质量警告(如跨日强赎快照说明)
    """
    if redeem_unusable:
        # §3.2 严格同日策略: R 不存在或 R≠D 时不用跨日强赎数据冒充同日,
        # 赎回相关字段一律按缺失处理(redeem_trade_date 仍由 meta 如实报告)
        redeem_map = {}
    redeem_map = redeem_map or {}
    cell_rows = _enrich_rows(rows, redeem_map, as_of_date)
    return _run_pipeline(
        cell_rows, template,
        blacklist_ids=blacklist_ids,
        trade_date=trade_date,
        redeem_trade_date=redeem_trade_date,
        redeem_loaded=redeem_loaded,
        redeem_unusable=redeem_unusable,
        base_warnings=warnings,
    )


def screen_bonds_live(
    records: list[dict[str, Any]],
    template: dict[str, Any],
    redeem_cells: list[dict[str, Any]] | None = None,
    *,
    as_of_date: date | None = None,
    blacklist_ids: list[str] | set[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """对实时集思录记录执行同一套筛选打分引擎(不落库)。

    records: fetch_cb_list() 返回的集思录 cell 列表
    redeem_cells: fetch_redeem_list() 返回的强赎 cell 列表, 可选;
      空列表按 §3.2 视为"赎回数据不可用"(gateway 以空列表表达降级,
      不区分成功空集与失败, 统一提示, 不虚构失败原因)。
    as_of_date: 上市天数计算基准(live 路径由路由显式传东八区当天)。
    实时无法确认交易日期: meta.trade_date=None, 不用当天日期冒充交易日。
    """
    redeem_map: dict[str, dict[str, Any]] = {}
    for cell in redeem_cells or []:
        bid = _normalize_bond_code_6(cell.get("bond_id"))
        if bid:
            redeem_map[bid] = cell
    redeem_unusable = not redeem_map
    cell_rows = _enrich_rows(records, redeem_map, as_of_date)
    return _run_pipeline(
        cell_rows, template,
        blacklist_ids=blacklist_ids,
        trade_date=None,
        redeem_trade_date=None,
        redeem_loaded=not redeem_unusable,
        redeem_unusable=redeem_unusable,
        base_warnings=warnings,
    )


def format_redeem_status(bond_cell: dict[str, Any], redeem_cell: dict[str, Any] | None) -> str:
    """格式化强赎状态(对齐 v2; 旧入口 cb_intraday 共用)。

    优先用 redeem_cell 的精确计数, fallback 到 bond_cell 的 icons 标记。
    """
    if redeem_cell:
        state = normalize_redeem_state(redeem_cell)
        label = state["status_label"]
        # None 与 "" 同样视为缺失(ORM 快照列 NULL 与实时源空串同口径)
        real = state["trigger_days_met"] or ""
        need = state["trigger_days_required"] or ""
        total = state["trigger_window_days"] or ""
        count_str = f"{real}/{need} | {total}" if (real != "" and need != "") else ""
        if label:
            if state["status_code"] == "TRIGGER_COUNTING" and state["trigger_days_remaining"] is not None:
                return f"至少还需 {state['trigger_days_remaining']} 天 {count_str}".strip()
            return f"{label} {count_str}".strip()

    icons = bond_cell.get("icons") or {}
    if any(icon in icons for icon in ("R", "O", "G", "B")):
        return "强赎状态待同步"
    return ""


# 因子目录重导出(向后兼容旧 import 路径; 目录唯一事实源在 cb_factors)
__all__ = [
    "RedeemDataUnavailableError",
    "screen_bonds",
    "screen_bonds_live",
    "format_redeem_status",
]
