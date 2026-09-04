# -*- coding: utf-8 -*-
"""可转债强赎列表数据抓取。

数据源: 集思录 cbnew/redeem_list 接口(POST, 需登录态)。
返回所有强赎相关转债的实时状态, 关键字段 redeem_remain_days 用于
redeem_safe_days(强赎临近触发天数)判断。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from backend.services.jisilu import get_cookie

CB_REDEEM_LIST_URL = "https://www.jisilu.cn/data/cbnew/redeem_list/"

CB_REDEEM_HEADERS = {
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


def fetch_redeem_list() -> list[dict[str, Any]]:
    """抓取集思录强赎列表全量数据。

    返回: [{bond_id, bond_nm, redeem_icon, redeem_remain_days, ...}, ...]
    字段为集思录原始 cell 字段, 全量保存不做筛选。
    """
    cookie = get_cookie()
    headers = {**CB_REDEEM_HEADERS, "Cookie": cookie}
    params = {"___jsl": f"LST___t={int(time.time() * 1000)}"}
    # rp=page size: 接口当前忽略该参数默认全量, 但显式给大值防哪天开始尊重分页时静默截断
    payload = {"rp": 1000, "page": 1}

    resp = httpx.post(
        CB_REDEEM_LIST_URL, headers=headers, params=params, data=payload, timeout=15
    )
    resp.raise_for_status()
    data = resp.json()
    rows = data.get("rows", [])

    records: list[dict[str, Any]] = []
    for row in rows:
        cell = row.get("cell", {}) if isinstance(row, dict) else {}
        if cell:
            records.append(cell)

    return records
