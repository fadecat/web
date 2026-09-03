# -*- coding: utf-8 -*-
"""可转债等权指数数据抓取。

数据源: 集思录 cb_index 页面(需登录态)。
页面内嵌 JS 变量 var __date / var __data,正则提取后按字段映射。

字段映射(对齐旧 history.py JISILU_FIELD_MAP):
  price -> index_value          等权指数(价格)
  mid_price -> median_price     价格中位数
  avg_price -> avg_price        平均价格
  avg_ytm_rt -> avg_ytm         平均到期收益率
  mid_convert_value -> median_convert_value  转股价值中位数
  avg_dblow -> avg_dblow        平均双低
  avg_premium_rt -> avg_premium  平均溢价率
  mid_premium_rt -> median_premium  溢价率中位数
  turnover_rt -> turnover_rate   换手率
  count -> count                转债数量
  temperature -> temperature     温度
  idx_price -> idx_price         等权指数(另一种)
  idx_increase_rt -> idx_increase_rt  指数涨跌幅
"""
from __future__ import annotations

import re
from typing import Any

from backend.services.jisilu import fetch_with_auth
from backend.utils import parse_float

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CB_INDEX_URL = "https://www.jisilu.cn/data/cbnew/cb_index/"

# 集思录原始字段名 -> 干净字段名
JISILU_FIELD_MAP: dict[str, str] = {
    "price": "index_value",
    "mid_price": "median_price",
    "avg_ytm_rt": "avg_ytm",
    "avg_price": "avg_price",
    "mid_convert_value": "median_convert_value",
    "avg_dblow": "avg_dblow",
    "avg_premium_rt": "avg_premium",
    "mid_premium_rt": "median_premium",
    "turnover_rt": "turnover_rate",
    "count": "count",
    "temperature": "temperature",
    "idx_price": "idx_price",
    "idx_increase_rt": "idx_increase_rt",
}


# ---------------------------------------------------------------------------
# 页面抓取 + 解析
# ---------------------------------------------------------------------------

def fetch_cb_index_page() -> str:
    """抓取集思录 cb_index 页面 HTML(自动带登录 cookie)。"""
    resp = fetch_with_auth(CB_INDEX_URL, timeout=15)
    resp.raise_for_status()
    return resp.text


def parse_cb_index_page(html: str) -> list[dict[str, Any]]:
    """解析 cb_index 页面 HTML,返回按日期排列的记录列表。

    输出: [{date, index_value, median_price, avg_price, avg_ytm, ...}, ...]
    """
    # 提取 var __date = ['2025-09-03', ...];
    m_date = re.search(r"var __date\s*=\s*(\[.*?\]);", html, re.DOTALL)
    if not m_date:
        raise RuntimeError("未找到 var __date 变量")
    dates = re.findall(r"'([^']*)'", m_date.group(1))

    # 提取 var __data = {'field': [v1, v2, ...], ...};
    m_data = re.search(r"var __data\s*=\s*\{([\s\S]*?)\};", html)
    if not m_data:
        raise RuntimeError("未找到 var __data 变量")
    pairs = re.findall(r"'([a-zA-Z_]+)'\s*:\s*\[([^\]]*)\]", m_data.group(1))

    series: dict[str, list[str]] = {}
    for key, values in pairs:
        series[key] = [v.strip() for v in values.split(",") if v.strip()]

    records: list[dict[str, Any]] = []
    for idx, date in enumerate(dates):
        record: dict[str, Any] = {"date": date}
        for jisilu_key, target_key in JISILU_FIELD_MAP.items():
            values = series.get(jisilu_key)
            if not values or idx >= len(values):
                continue
            record[target_key] = values[idx]
        records.append(record)

    return records


def fetch_cb_index_history() -> list[dict[str, Any]]:
    """一站式: 抓取 + 解析可转债等权指数历史数据。

    返回: [{date, index_value, median_price, avg_price, avg_ytm,
            median_convert_value, avg_dblow, avg_premium, median_premium,
            turnover_rate, count, temperature, idx_price, idx_increase_rt}, ...]
    """
    html = fetch_cb_index_page()
    records = parse_cb_index_page(html)
    if not records:
        raise ValueError("可转债等权指数页面未返回有效数据")
    return records
