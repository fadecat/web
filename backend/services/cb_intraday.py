# -*- coding: utf-8 -*-
"""盘中选债服务: 实时拉集思录 → 内存纯过滤, 不落库、不打分、不排序。

与收盘筛选(screen_bonds 走 DB 快照+排名打分)的差异:
- 数据源: 实时 cb_list_new + redeem_list(并行拉取), 价格/双低/溢价率/强赎计数全部是当次请求的最新值
- 纯条件过滤: 区间/单边字段筛选(现价/转换价值/溢价率/评级/年限/规模/到期收益率),
  返回全部通过条件的债, 顺序=集思录自然顺序(其默认按双低升序)
- 不落库: 盘中数据纯内存流转, 快照表的"收盘后定时任务"语义不被污染
- 时点元信息: 返回 quote_time(集思录行情时点) 供前端展示数据时点

到期收益率(简化口径, 区别于集思录 YTM):
    ytm_simple = (到期赎回价 − 现价) / 现价 × 100
    即"持有到期的总回报率"(未年化): 假设持有到期按赎回价兑付,
    不计各期利息与税, 也不做年限年化。赎回价缺失时不计算。
    各平台 YTM 口径参差(计息/扣税/年化差异), 用该简化口径保证内部一致性。

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

    强赎列表失败不阻塞选债(仅赎回价/保本价差/到期收益率/强赎状态列缺数据),
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


# 条件过滤映射: 结果行字段 → (下限参数名, 上限参数名); 单边条件另一侧为 None
_FILTER_SCHEMA: dict[str, tuple[str | None, str | None]] = {
    "price": ("price_min", "price_max"),
    "convert_value": ("convert_value_min", "convert_value_max"),
    "premium_rt": (None, "premium_rt_max"),
    "curr_iss_amt": (None, "curr_iss_amt_max"),
    "year_left": ("year_left_min", "year_left_max"),
    "ytm_simple": ("ytm_min", None),
}


def _passes_filters(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    """数值区间/单边 + 评级枚举过滤(作用于结果行)。

    区间一端为 None 表示不限制;
    字段值缺失时: 有下限条件(≥)则排除(保本类指标缺数据不放行),
    仅有上限条件(≤)则放行(不误排除)。
    """
    for field, (min_key, max_key) in _FILTER_SCHEMA.items():
        lo = filters.get(min_key) if min_key else None
        hi = filters.get(max_key) if max_key else None
        if lo is None and hi is None:
            continue
        val = _num(row.get(field))
        if val is None:
            # 保本类条件(到期收益率≥)下缺数据=不满足保本要求, 排除
            if lo is not None:
                return False
            continue
        if lo is not None and val < lo:
            return False
        if hi is not None and val > hi:
            return False

    ratings = filters.get("ratings") or []
    if ratings:
        if str(row.get("rating") or "").strip() not in ratings:
            return False

    return True


def _live_row(rec: dict[str, Any], redeem_cell: dict[str, Any] | None) -> dict[str, Any]:
    """实时记录 → 结果行 dict(含到期收益率简化口径计算)。"""
    price = _num(rec.get("price"))
    redeem_price = _num((redeem_cell or {}).get("redeem_price"))
    year_left = _num(rec.get("year_left"))

    # 到期收益率(简化): (赎回价-现价)/现价, 持有到期总回报率(未年化); 赎回价缺失不算
    ytm_simple: float | None = None
    if redeem_price is not None and price is not None and price > 0:
        ytm_simple = round((redeem_price - price) / price * 100, 3)

    return {
        "code": str(rec.get("bond_id") or ""),
        "name": str(rec.get("bond_nm") or ""),
        "price": price,
        "change_rt": _num(rec.get("increase_rt")),
        "dblow": _num(rec.get("dblow")),
        "premium_rt": _num(rec.get("premium_rt")),
        "curr_iss_amt": _num(rec.get("curr_iss_amt")),
        "convert_value": _num(rec.get("convert_value")),
        "year_left": year_left,
        "pb": _num(rec.get("pb")),
        "ytm_rt": _num(rec.get("ytm_rt")),
        "rating": str(rec.get("rating_cd") or ""),
        "redeem_price": redeem_price,
        "ytm_simple": ytm_simple,
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
        price_min/price_max, convert_value_min/max, premium_rt_max,
        curr_iss_amt_max, year_left_min/max, ytm_min, ratings: list[str]
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

    # 1. 全量转结果行(附 redeem_price/ytm_simple/redeem_gap)
    rows = [
        _live_row(rec, redeem_map.get(str(rec.get("bond_id") or "").strip()))
        for rec in records
    ]

    # 2. 纯条件过滤
    passed = [row for row in rows if _passes_filters(row, filters)]

    # 行情时点: 集思录每只债有 last_time(HH:MM:SS), 取最大值近似全表时点
    quote_times = [
        str(c.get("last_time")).strip()
        for c in records
        if c.get("last_time")
    ]
    return {
        "total_all": len(records),
        "total_filtered": len(passed),
        "rows": passed,
        "intraday": {
            "fetched_at": datetime.now().strftime("%H:%M:%S"),
            "quote_time": max(quote_times) if quote_times else None,
            "total_live": len(records),
            "redeem_loaded": bool(redeem_cells),
        },
    }
