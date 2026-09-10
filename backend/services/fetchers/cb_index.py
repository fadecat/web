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

def _split_series_values(values: str) -> list[str]:
    """把源数组字符串切分为 token 列表, 保留中间空位(日期对齐的关键)。

    规则:
    - 按逗号切分并对每个 token strip; 中间空串(如 '100,,102' 的第二个)
      保留为空字符串, 对应这一天该字段缺值, 不能丢弃否则后续元素错位。
    - 尾随逗号(如 '100,102,')只移除最后一个空 token(JS 语法允许的尾逗号)。
    - 空字符串(源 '[]')视为空数组 -> 返回 [], 该字段按「全缺失」处理。

    这是纯文本切分, 不解析 JS; 数值/None 的转化交给后端 utils.parse_float。
    """
    tokens = [v.strip() for v in values.split(",")]
    # 仅当逗号后只有空白且最后一个 token 为空时移除 JS 语法尾 token
    if values.rstrip().endswith(",") and tokens and tokens[-1] == "":
        tokens = tokens[:-1]
    # 只有空白内容才是源 '[]'; '[,]' 是长度为 1 的空位数组, 必须保留
    if values.strip() == "":
        tokens = []
    return tokens


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
        series[key] = _split_series_values(values)

    # 一个已知字段都映射不上 = 数据源字段变更, 否则会产出「只有日期、指标全 None」
    # 的记录被静默落库并记成功
    if not any(key in series for key in JISILU_FIELD_MAP):
        raise ValueError("可转债等权指数页面字段全部无法映射,数据源字段可能已变更")

    # 已出现且非空的已知字段数组长度必须与日期数相同, 否则按日期下标取数会错位。
    # 空数组(源 '[]')视为该字段全缺失, 不做长度校验。
    for jisilu_key in JISILU_FIELD_MAP:
        if jisilu_key not in series:
            continue
        arr = series[jisilu_key]
        if not arr:
            continue
        if len(arr) != len(dates):
            raise ValueError(
                f"等权指数字段 '{jisilu_key}' 数组长度 {len(arr)} 与日期数 "
                f"{len(dates)} 不一致, 数据源分页/截断可能导致错位"
            )

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
