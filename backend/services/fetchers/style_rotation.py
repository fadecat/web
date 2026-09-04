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

from backend.utils import (
    DEFAULT_HEADERS,
    fetch_with_retry,
    parse_float,
)

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
        limit: 最大 K 线数量(腾讯接口单次上限约 2000,超出会返回异常格式)

    返回: [{date, open, close, high, low, volume}, ...] 升序排列。

    腾讯返回格式(每行): [日期, 开, 收, 高, 低, 成交量, {}, 换手率, 成交额, ...]

    注意: 腾讯接口语义是「end_date 往前数 limit 条」而非「start_date 起往后数」,
    当 (end - start) 区间内 K 线数超过 limit 时,返回的是区间尾部 limit 条。
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

    # 带指数退避重试: 网络抖动/超时/5xx/429 自动重试, 4xx 直接抛出
    resp = fetch_with_retry(
        "GET", TENCENT_KLINE_URL, params=params, headers=headers,
    )
    resp.raise_for_status()

    # 响应是 JS 赋值格式: kline_day={...}
    text = resp.text
    json_text = text[text.find("={") + 1:]
    payload = json.loads(json_text)

    inner = payload.get("data", {}).get(symbol, {})
    if not isinstance(inner, dict):
        # limit 超过腾讯上限(~2000)时返回格式会变成 list,明确报错提示调用方分段
        raise ValueError(
            f"腾讯K线接口返回异常格式(limit 超上限?): {code}, 请减小 limit 或分段抓取"
        )
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


def fetch_index_kline_auto(
    code: str,
    start_date: str = "2013-01-01",
    end_date: str = "",
) -> list[dict[str, Any]]:
    """自动分段拉取长历史 K 线(绕过腾讯单次 ~2000 条上限)。

    按年分段请求(每段最多约 250 个交易日, 远低于上限), 合并去重后返回。
    用于历史回补; 日频增量任务用 fetch_index_kline 即可。
    """
    from datetime import datetime, timedelta

    end_dt = (
        datetime.fromisoformat(end_date)
        if end_date
        else datetime.now()
    )
    start_dt = datetime.fromisoformat(start_date)

    all_records: dict[str, dict[str, Any]] = {}
    seg_start = start_dt
    while seg_start <= end_dt:
        # 每段到年底
        seg_end_year = min(seg_start.year, end_dt.year)
        seg_end = datetime(seg_end_year, 12, 31)
        if seg_end > end_dt:
            seg_end = end_dt

        rows = fetch_index_kline(
            code,
            start_date=seg_start.strftime("%Y-%m-%d"),
            end_date=seg_end.strftime("%Y-%m-%d"),
            limit=400,
        )
        for r in rows:
            all_records[r["date"]] = r

        seg_start = seg_end + timedelta(days=1)

    result = sorted(all_records.values(), key=lambda r: r["date"])
    return result
