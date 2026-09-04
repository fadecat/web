# -*- coding: utf-8 -*-
"""盘中选债服务: 实时拉集思录 → 内存纯过滤, 不落库、不打分、不排序。

与收盘筛选(screen_bonds 走 DB 快照+排名打分)的差异:
- 数据源: 实时 cb_list_new + redeem_list(并行拉取), 价格/双低/溢价率/强赎计数全部是当次请求的最新值
- 纯条件过滤: 六字段区间筛选(现价/转换价值/溢价率/评级/年限/规模),
  返回全部通过条件的债, 顺序=集思录自然顺序(其默认按双低升序)
- 不落库: 盘中数据纯内存流转, 快照表的"收盘后定时任务"语义不被污染
- 时点元信息: 返回 quote_time(集思录行情时点) 供前端展示数据模式徽章

盘后调用同样有意义: 集思录收盘后列表接口返回当日收盘价,
比"上一次日频任务的快照"更新(任务失败/漏跑的兜底)。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from loguru import logger

from backend.services.cb_screen import format_redeem_status
from backend.services.fetchers.cb_list import fetch_cb_list
from backend.services.fetchers.cb_redeem import fetch_redeem_list


def _fetch_both() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """并行拉取实时转债列表与强赎列表。

    强赎列表失败不阻塞选债(仅赎回价/保本价差/强赎状态列缺数据),
    转债列表失败直接抛(那是主数据, 没得筛)。
    """
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_list = ex.submit(fetch_cb_list)
        f_redeem = ex.submit(fetch_redeem_list)

        records = f_list.result()  # 失败会抛, 由上层转 HTTPException

        try:
            redeem_cells = f_redeem.result()
        except Exception as exc:
            logger.warning(f"盘中选债: 强赎列表拉取失败(降级为无强赎数据): {exc}")
            redeem_cells = []

    return records, redeem_cells


def _num(value: Any) -> float | None:
    """安全转 float, None/空串/'-' 返回 None。"""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


# 区间过滤字段: field → (min 参数名, max 参数名)
_RANGE_FILTERS: dict[str, tuple[str, str]] = {
    "price": ("price_min", "price_max"),
    "convert_value": ("convert_value_min", "convert_value_max"),
    "premium_rt": ("premium_rt_min", "premium_rt_max"),
    "year_left": ("year_left_min", "year_left_max"),
    "curr_iss_amt": ("curr_iss_amt_min", "curr_iss_amt_max"),
}


def _passes_filters(rec: dict[str, Any], filters: dict[str, Any]) -> bool:
    """数值区间 + 评级枚举过滤。

    区间一端为 None 表示不限制; 字段值缺失时放行(不误排除)。
    """
    for field, (min_key, max_key) in _RANGE_FILTERS.items():
        lo, hi = filters.get(min_key), filters.get(max_key)
        if lo is None and hi is None:
            continue
        val = _num(rec.get(field))
        if val is None:
            continue
        if lo is not None and val < lo:
            return False
        if hi is not None and val > hi:
            return False

    ratings = filters.get("ratings") or []
    if ratings:
        if str(rec.get("rating_cd") or "").strip() not in ratings:
            return False

    return True


def _live_row(rec: dict[str, Any], redeem_cell: dict[str, Any] | None) -> dict[str, Any]:
    """实时记录 → 结果行 dict(与收盘筛选结果行字段对齐, 少 rank/score/selected)。"""
    price = _num(rec.get("price"))
    redeem_price = _num((redeem_cell or {}).get("redeem_price"))
    return {
        "code": str(rec.get("bond_id") or ""),
        "name": str(rec.get("bond_nm") or ""),
        "price": price,
        "change_rt": _num(rec.get("increase_rt")),
        "dblow": _num(rec.get("dblow")),
        "premium_rt": _num(rec.get("premium_rt")),
        "curr_iss_amt": _num(rec.get("curr_iss_amt")),
        "convert_value": _num(rec.get("convert_value")),
        "year_left": _num(rec.get("year_left")),
        "pb": _num(rec.get("pb")),
        "ytm_rt": _num(rec.get("ytm_rt")),
        "rating": str(rec.get("rating_cd") or ""),
        "redeem_price": redeem_price,
        # 保本价差 = 到期赎回价 - 现价, 正数越大保本垫越厚(负数=现价已高于赎回价)
        "redeem_gap": (
            round(redeem_price - price, 3)
            if redeem_price is not None and price is not None
            else None
        ),
        "redeem": format_redeem_status(rec, redeem_cell),
    }


def screen_bonds_intraday(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    """盘中选债: 实时数据 + 前端筛选条件 → 纯过滤结果。

    filters 键:
        price_min/price_max, convert_value_min/max, premium_rt_min/max,
        year_left_min/max, curr_iss_amt_min/max, ratings: list[str]
    全部可选; 不传条件 = 返回全量。

    返回: {total_all, total_filtered, rows, intraday: {fetched_at, quote_time, total_live, redeem_loaded}}
    """
    filters = filters or {}
    records, redeem_cells = _fetch_both()

    redeem_map: dict[str, dict[str, Any]] = {}
    for cell in redeem_cells:
        bid = str(cell.get("bond_id") or "").strip()
        if bid:
            redeem_map[bid] = cell

    passed = [rec for rec in records if _passes_filters(rec, filters)]
    rows = [
        _live_row(rec, redeem_map.get(str(rec.get("bond_id") or "").strip()))
        for rec in passed
    ]

    # 行情时点: 集思录每只债有 last_time(HH:MM:SS), 取最大值近似全表时点
    quote_times = [
        str(c.get("last_time")).strip()
        for c in records
        if c.get("last_time")
    ]
    return {
        "total_all": len(records),
        "total_filtered": len(passed),
        "rows": rows,
        "intraday": {
            "fetched_at": datetime.now().strftime("%H:%M:%S"),
            "quote_time": max(quote_times) if quote_times else None,
            "total_live": len(records),
            "redeem_loaded": bool(redeem_cells),
        },
    }
