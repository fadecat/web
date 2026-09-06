# -*- coding: utf-8 -*-
"""指数日线收盘价(eod)抓取 —— 易方达 CDN 源。

数据源: https://cdn.efunds.com.cn/etf-net/index_eod_price_{code}.json
特点: 单文件全历史(基日至今), 每次运行=全量同步, 幂等落库后漏跑自动自愈。
      与估值板块的 index_valuation_percentile_{code}.json 是同族 CDN 接口。

标的清单: config/index_eod.yaml(YAML 驱动, 加标的只改配置)。
当前标的: 930955 红利低波动100 / 399296 创成长(风格轮动红利对照组)。

注意: 腾讯 K 线源拿不到 930955(中证指数), 399296 也只有 2019 年后,
      所以这两只用易方达源; 大小盘(399376/399373)仍走腾讯源
      (见 fetchers/style_rotation.py), 两路数据同入 IndexDailyQuote 表。
"""
from __future__ import annotations

import json
from typing import Any

from backend.utils import (
    DEFAULT_HEADERS,
    fetch_with_retry,
    parse_float,
)

EOD_PRICE_URL_TEMPLATE = (
    "https://cdn.efunds.com.cn/etf-net/index_eod_price_{index_code}.json"
)


def build_eod_price_url(index_code: str) -> str:
    """构造易方达指数日收盘价 JSON 的 CDN URL。"""
    digits = str(index_code).strip()
    if not digits.isdigit():
        raise ValueError(f"无法识别指数代码: {index_code}")
    return EOD_PRICE_URL_TEMPLATE.format(index_code=digits)


def fetch_index_eod_price(index_code: str, url: str = "") -> list[dict[str, Any]]:
    """抓取易方达指数日收盘价全历史。

    参数:
        index_code: 指数代码(如 "930955")
        url: 可选,直接指定完整 URL(跳过自动构造)

    返回: 按日期升序的列表,结构与 fetch_index_kline(腾讯版)对齐:
          [{date, open, close, high, low, volume}, ...]
          易方达源仅有 close, open/high/low/volume 恒为 None
          (IndexDailyQuote 各列本就 nullable, 落库层无需改动)。

    源 JSON 每行形如:
          {"trdCode": "930955", "trdDt": "2026-09-04",
           "pxClose": 11412.7555, "pctChg1D": 0.51955534}
    """
    source_url = url.strip() if url else build_eod_price_url(index_code)
    resp = fetch_with_retry(
        "GET", source_url,
        headers={**DEFAULT_HEADERS, "Referer": "https://www.etf.com.cn/"},
    )
    resp.raise_for_status()

    rows = resp.json()
    if not isinstance(rows, list):
        raise ValueError(f"易方达 eod 接口返回格式异常(非 list): {index_code}")

    records: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        trade_date = str(row.get("trdDt") or "").strip()
        close = parse_float(row.get("pxClose"))
        if not trade_date or close is None:
            continue
        records.append({
            "date": trade_date,
            "open": None,
            "close": close,
            "high": None,
            "low": None,
            "volume": None,
        })

    if not records:
        raise ValueError(f"易方达 eod 接口未返回有效数据: {index_code}")

    records.sort(key=lambda r: r["date"])
    return records
