# -*- coding: utf-8 -*-
"""可转债全市场快照数据抓取。

数据源: 集思录 cb_list_new 接口(POST, 需登录态)。
每天一条全量快照: 当日所有转债的完整数据。
后续三低等策略基于此数据计算,不在抓取层做策略逻辑。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from backend.services.jisilu import get_cookie
from backend.utils import parse_float

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CB_LIST_URL = "https://www.jisilu.cn/data/cbnew/cb_list_new/"
CB_PAGE_SIZE = 1000

CB_ALLOWED_RATINGS = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-"]
CB_ALLOWED_MARKETS = ["shmb", "shkc", "szmb", "szcy"]

CB_FORM_DATA = {
    "fprice": "", "tprice": "", "curr_iss_amt": "", "convert_amt_ratio": "",
    "premium_rt": "", "fyear_left": "", "tyear_left": "",
    "rating_cd[]": CB_ALLOWED_RATINGS,
    "is_search": "Y",
    "market_cd[]": CB_ALLOWED_MARKETS,
    "show_blocked": "N", "min_price_only": "N", "btype": "",
    "listed": "Y", "qflag": "N", "sw_cd": "", "bond_ids": "",
    "rp": str(CB_PAGE_SIZE),
}

CB_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "https://www.jisilu.cn/data/cbnew/",
    "Origin": "https://www.jisilu.cn",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


def fetch_cb_list() -> list[dict[str, Any]]:
    """抓取集思录可转债全市场快照。

    POST cb_list_new,返回全部在市转债的 cell 数据。
    ≤30 条视为未登录/会话失效,抛错。

    返回: [{bond_id, bond_nm, price, convert_value, premium_rt, ...}, ...]
    字段为集思录原始 cell 字段(64 个),全量保存不做筛选。
    """
    cookie = get_cookie()
    headers = {**CB_HEADERS, "Cookie": cookie}
    params = {"___jsl": f"LST___t={int(time.time() * 1000)}"}

    resp = httpx.post(CB_LIST_URL, headers=headers, params=params, data=CB_FORM_DATA, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    rows = data.get("rows", [])

    if len(rows) <= 30:
        raise ValueError(f"转债列表仅返回 {len(rows)} 条(≤30),可能未登录或会话已失效")

    # 提取 cell 字段
    records: list[dict[str, Any]] = []
    for row in rows:
        cell = row.get("cell", {}) if isinstance(row, dict) else {}
        if cell:
            records.append(cell)

    return records
