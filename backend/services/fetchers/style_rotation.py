# -*- coding: utf-8 -*-
"""风格轮动板块数据抓取。

数据源: 腾讯证券日线 K 线接口(proxy.finance.qq.com)。
标的: 399376 国证小盘成长 vs 399373 国证大盘价值。
计算: 各自 250 交易日收益率 → 差值(spread)。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import httpx

from backend.utils import DEFAULT_HEADERS, DEFAULT_TIMEOUT, parse_float

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

TENCENT_KLINE_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"

# 风格轮动标的
LEFT_SYMBOL = "399376"
LEFT_NAME = "国证小盘成长"
RIGHT_SYMBOL = "399373"
RIGHT_NAME = "国证大盘价值"

# 计算参数(对齐旧 style_rotation.py)
RETURN_WINDOW_DAYS = 250      # 250 交易日收益率
DISPLAY_WINDOW_DAYS = 252 * 5  # 展示窗口 5 年


# ---------------------------------------------------------------------------
# 腾讯 K 线取数
# ---------------------------------------------------------------------------

def _build_tencent_symbol(code: str) -> str:
    """东财/标准代码 -> 腾讯格式(sz399376 / sh600000)。"""
    digits = code.strip()
    if digits[0] in {"5", "6", "9"}:
        return f"sh{digits}"
    return f"sz{digits}"


def fetch_index_kline(
    code: str,
    start_date: str = "",
    end_date: str = "",
    limit: int = 640,
) -> list[dict[str, Any]]:
    """从腾讯证券拉取指数日线 K 线。

    参数:
        code: 指数代码(如 "399376")
        start_date: 起始日期 "YYYY-MM-DD"(空串不限)
        end_date: 结束日期 "YYYY-MM-DD"(空串不限)
        limit: 最大 K 线数量

    返回: [{date, open, close, high, low, volume}, ...] 升序排列。

    腾讯返回格式(每行): [日期, 开, 收, 高, 低, 成交量, {}, 换手率, 成交额, ...]
    """
    symbol = _build_tencent_symbol(code)
    if not start_date:
        start_date = "2020-01-01"
    if not end_date:
        end_date = "2027-12-31"

    params = {
        "_var": "kline_day",
        "param": f"{symbol},day,{start_date},{end_date},{limit},",
        "r": "0.8205512681390605",
    }
    headers = {**DEFAULT_HEADERS, "Referer": "https://gu.qq.com/"}

    resp = httpx.get(TENCENT_KLINE_URL, params=params, headers=headers, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()

    # 响应是 JS 赋值格式: kline_day={...}
    text = resp.text
    json_text = text[text.find("={") + 1:]
    payload = json.loads(json_text)

    inner = payload.get("data", {}).get(symbol, {})
    klines = inner.get("day", []) or inner.get("qfqday", []) or inner.get("hfqday", [])

    if not klines:
        raise ValueError(f"腾讯K线接口未返回数据: {code}")

    records: list[dict[str, Any]] = []
    for row in klines:
        if not isinstance(row, list) or len(row) < 5:
            continue
        records.append({
            "date": str(row[0]),
            "open": parse_float(row[1]),
            "close": parse_float(row[2]),
            "high": parse_float(row[3]),
            "low": parse_float(row[4]),
            "volume": parse_float(row[5]) if len(row) > 5 else None,
        })

    records.sort(key=lambda r: r["date"])
    return records
