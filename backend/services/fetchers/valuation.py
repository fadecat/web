# -*- coding: utf-8 -*-
"""市场估值板块数据抓取。

从旧 market-daily 提取重写的纯函数,职责边界:
- fetch_index_detail        : 易方达指数详情(PE/PB/PS 及分位)
- fetch_index_dividend_yield: 独立股息率 JSON(可选覆盖)
- fetch_cn_10y_bond_yield   : 10 年期国债收益率(股债利差右轴)

设计原则:
1. 纯函数: 入参为 URL/代码,出参为可序列化 dict,不碰 DB/不碰全局状态。
2. 与调度解耦: 何时抓取由 scheduler 或 API 触发决定。
3. httpx 替代 requests,标准库 datetime 替代 pandas。
4. 去掉归档回退/重试装饰器(调度层/存储层职责)。

字段对齐旧实现中的 INDEX_VALUATION_METRIC_FIELDS,详见 docs/web-refactor.md。
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import httpx

from backend.utils import (
    DEFAULT_HEADERS,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    extract_index_digits,
    parse_float,
    parse_optional_date,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

INDEX_DETAIL_URL_TEMPLATE = (
    "https://www.etf.com.cn/api/etf-api-service/index/detail?indexCode={index_code}"
)
INDEX_DIVIDEND_YIELD_URL_TEMPLATE = (
    "https://cdn.efunds.com.cn/etf-net/index_dividend_ratio_{index_code}.json"
)
INDEX_VALUATION_PERCENTILE_URL_TEMPLATE = (
    "https://cdn.efunds.com.cn/etf-net/index_valuation_percentile_{index_code}.json"
)

# 估值指标字段映射: {显示名: {当前值字段, {分位标签: 源字段}}}
INDEX_VALUATION_METRIC_FIELDS: dict[str, dict[str, Any]] = {
    "PE(TTM)": {
        "current": "pETtm",
        "percentiles": {
            "3M": "pETtm3M",
            "6M": "pETtm6M",
            "1Y": "pETtm1Y",
            "2Y": "pETtm2Y",
            "3Y": "pETtm3Y",
            "5Y": "pETtm5Y",
            "10Y": "pETtm10Y",
            "今年以来": "pETtmTY",
            "成立以来": "pETtmBgn",
        },
    },
    "PB(LF)": {
        "current": "pBLf",
        "percentiles": {
            "3M": "pBLf3M",
            "6M": "pBLf6M",
            "1Y": "pBLf1Y",
            "2Y": "pBLf2Y",
            "3Y": "pBLf3Y",
            "5Y": "pBLf5Y",
            "10Y": "pBLf10Y",
            "今年以来": "pBLfTY",
            "成立以来": "pBLfBgn",
        },
    },
    "PS(TTM)": {
        "current": "pSTtm",
        "percentiles": {
            "3M": "pSTtm3M",
            "6M": "pSTtm6M",
            "1Y": "pSTtm1Y",
            "2Y": "pSTtm2Y",
            "3Y": "pSTtm3Y",
            "5Y": "pSTtm5Y",
            "10Y": "pSTtm10Y",
            "今年以来": "pSTtmTY",
            "成立以来": "pSTtmBgn",
        },
    },
}


# ---------------------------------------------------------------------------
# 通用 HTTP 取数
# ---------------------------------------------------------------------------

def fetch_json(url: str, *, timeout: float = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES) -> Any:
    """GET 请求返回 JSON,带重试。5xx 自动重试。

    重试时追加 ``_=N`` 参数绕过 CDN 缓存(海外边缘偶发把 502 缓存住)。
    """
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        request_url = url
        if attempt > 1:
            sep = "&" if "?" in url else "?"
            request_url = f"{url}{sep}_={attempt}"
        try:
            resp = httpx.get(request_url, headers=DEFAULT_HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(0.5 * attempt)
    raise RuntimeError(f"请求失败({retries}次重试): {url} -> {last_exc}")


# ---------------------------------------------------------------------------
# URL 构造
# ---------------------------------------------------------------------------

def build_index_detail_url(index_code: str) -> str:
    digits = extract_index_digits(index_code)
    if not digits:
        raise ValueError(f"无法识别指数代码: {index_code}")
    return INDEX_DETAIL_URL_TEMPLATE.format(index_code=digits)


def build_index_dividend_yield_url(index_code: str) -> str:
    digits = extract_index_digits(index_code)
    if not digits:
        raise ValueError(f"无法识别指数代码: {index_code}")
    return INDEX_DIVIDEND_YIELD_URL_TEMPLATE.format(index_code=digits)


def build_index_valuation_percentile_url(index_code: str) -> str:
    digits = extract_index_digits(index_code)
    if not digits:
        raise ValueError(f"无法识别指数代码: {index_code}")
    return INDEX_VALUATION_PERCENTILE_URL_TEMPLATE.format(index_code=digits)


# ---------------------------------------------------------------------------
# 指数详情: 解析 + 取数
# ---------------------------------------------------------------------------

def parse_index_detail_response(payload: object, fallback_index_code: str = "") -> dict[str, Any]:
    """解析易方达指数详情接口返回的 JSON。

    输出结构:
    {
        "index_code": "930955",
        "index_name": "中证红利低波动指数",
        "index_short_name": "...",
        "index_type": "...",
        "index_dividend_yield_url": "https://cdn.efunds.com.cn/...",
        "index_valuation_percentile_url": "https://cdn.efunds.com.cn/...",
    }
    """
    if not isinstance(payload, dict):
        raise ValueError("指数详情接口返回格式异常(非 dict)")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("指数详情接口缺少 data 字段")

    return {
        "index_code": str(data.get("trdCode") or fallback_index_code).strip(),
        "index_name": str(data.get("indexName") or "").strip(),
        "index_short_name": str(data.get("indexSht") or "").strip(),
        "index_type": str(data.get("indexType") or "").strip(),
        "index_dividend_yield_url": str(data.get("dividendRatioJson") or "").strip(),
        "index_valuation_percentile_url": str(data.get("valuationPercentileJson") or "").strip(),
    }


def fetch_index_detail(index_code: str, url: str = "") -> dict[str, Any]:
    """抓取易方达指数详情接口。

    参数:
        index_code: 指数代码(如 "930955" / "sh930955" / "930955.SH")
        url: 可选,直接指定完整 URL(跳过自动构造)

    返回: 解析后的 dict,含 index_code / index_name / 股息率 URL / 估值分位 URL
    """
    source_url = url.strip() if url else build_index_detail_url(index_code)
    payload = fetch_json(source_url)
    result = parse_index_detail_response(payload, fallback_index_code=index_code)
    result["index_detail_url"] = source_url
    return result


# ---------------------------------------------------------------------------
# 股息率: 解析 + 取数
# ---------------------------------------------------------------------------

def parse_index_dividend_yield_rows(
    rows: object,
    fallback_index_code: str = "",
) -> dict[str, Any]:
    """解析易方达股息率 JSON(数组),计算分位与 5Y 均值。

    输入: 易方达 CDN JSON,形如 [{"trdDt": "2024-01-15", "dividendYield": 4.32, "trdCode": "930955"}, ...]

    输出:
    {
        "index_code": "930955",
        "index_dividend_yield": 4.32,         # 最新股息率
        "index_dividend_yield_date": "2024-01-15",
        "index_dividend_yield_percentiles": {"1Y": 35.2, "3Y": 48.1, "5Y": 52.3, "10Y": 61.0},
        "index_dividend_yield_average_5y": 4.15,
    }

    算法(对齐旧实现):
    - 按 trdDt 升序排列,取最后一条作为最新。
    - 分位 = 窗口内 <= 当前值的占比 * 100,窗口样本 < 20 则跳过。
    - 5Y 均值 = 最近 5 年窗口内所有值的算术平均。
    """
    if not isinstance(rows, list):
        raise ValueError("股息率接口返回格式异常(非 list)")

    records: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        dividend_yield = parse_float(row.get("dividendYield"))
        trade_date = parse_optional_date(row.get("trdDt"))
        if dividend_yield is None or trade_date is None:
            continue
        records.append({
            "date": trade_date,
            "yield": dividend_yield,
            "code": str(row.get("trdCode") or fallback_index_code).strip(),
        })

    if not records:
        raise ValueError("股息率接口未返回有效数据")

    # 按日期升序排列(旧代码用 df.sort_values("date"))
    records.sort(key=lambda r: r["date"])

    latest = records[-1]
    latest_yield = latest["yield"]
    latest_date = latest["date"]
    index_code = latest["code"] or fallback_index_code

    result: dict[str, Any] = {
        "index_code": index_code,
        "index_dividend_yield": latest_yield,
        "index_dividend_yield_date": latest_date.isoformat(),
    }

    # 分位计算: 1Y/3Y/5Y/10Y 窗口内 <= 当前值的占比
    percentiles: dict[str, float] = {}
    for label, years in [("1Y", 1), ("3Y", 3), ("5Y", 5), ("10Y", 10)]:
        cutoff = date(latest_date.year - years, latest_date.month, latest_date.day)
        window = [r["yield"] for r in records if r["date"] >= cutoff]
        if len(window) >= 20:
            count_below = sum(1 for v in window if v <= latest_yield)
            percentiles[label] = round(count_below / len(window) * 100, 2)
    if percentiles:
        result["index_dividend_yield_percentiles"] = percentiles

    # 5Y 均值
    cutoff_5y = date(latest_date.year - 5, latest_date.month, latest_date.day)
    avg_window = [r["yield"] for r in records if r["date"] >= cutoff_5y]
    if avg_window:
        result["index_dividend_yield_average_5y"] = round(sum(avg_window) / len(avg_window), 4)

    return result


def fetch_index_dividend_yield(index_code: str, url: str = "") -> dict[str, Any]:
    """抓取独立股息率 JSON 并解析。

    参数:
        index_code: 指数代码(如 "930955")
        url: 可选,直接指定完整 URL(跳过自动构造)

    返回: parse_index_dividend_yield_rows 的输出 + index_dividend_yield_source
    """
    source_url = url.strip() if url else build_index_dividend_yield_url(index_code)
    payload = fetch_json(source_url)
    result = parse_index_dividend_yield_rows(payload, fallback_index_code=index_code)
    result["index_dividend_yield_source"] = source_url
    return result


# ---------------------------------------------------------------------------
# 估值分位(PE/PB/PS): 解析 + 取数
# ---------------------------------------------------------------------------

def parse_index_valuation_percentile_rows(
    rows: object,
    fallback_index_code: str = "",
) -> list[dict[str, Any]]:
    """解析易方达估值分位 JSON(数组),返回全部历史交易日的 PE/PB/PS 及分位。

    输入: CDN JSON,形如 [{"trdDt": "2024-01-15", "pETtm": 15.2, "pETtm1Y": 30.5, ...}, ...]

    输出: 按日期升序排列的列表,每项:
    {
        "index_code": "930955",
        "trade_date": "2024-01-15",
        "metrics": {
            "PE(TTM)": {"current": 15.2, "percentiles": {"3M": ..., "1Y": 30.5, ...}},
            "PB(LF)":  {"current": 1.8,  "percentiles": {"3M": ..., "1Y": 25.0, ...}},
            "PS(TTM)": {"current": 2.1,  "percentiles": {"3M": ..., "1Y": 40.0, ...}},
        }
    }

    用 INDEX_VALUATION_METRIC_FIELDS 映射出当前值 + 9 个周期分位。
    """
    if not isinstance(rows, list):
        raise ValueError("估值分位接口返回格式异常(非 list)")

    records: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        trade_date = parse_optional_date(row.get("trdDt"))
        if trade_date is None:
            continue

        metrics: dict[str, dict[str, Any]] = {}
        for metric_name, fields in INDEX_VALUATION_METRIC_FIELDS.items():
            current = parse_float(row.get(fields["current"]))
            percentiles = {
                label: parse_float(row.get(source_field))
                for label, source_field in fields["percentiles"].items()
            }
            if current is not None or any(v is not None for v in percentiles.values()):
                metrics[metric_name] = {
                    "current": current,
                    "percentiles": percentiles,
                }

        if not metrics:
            continue

        records.append({
            "index_code": str(row.get("trdCode") or fallback_index_code).strip(),
            "trade_date": trade_date.isoformat(),
            "metrics": metrics,
        })

    if not records:
        raise ValueError("估值分位接口未返回有效数据")

    records.sort(key=lambda r: r["trade_date"])
    return records


def fetch_index_valuation_percentile(index_code: str, url: str = "") -> list[dict[str, Any]]:
    """抓取易方达估值分位 JSON 并解析全部历史日期。

    参数:
        index_code: 指数代码(如 "930955")
        url: 可选,直接指定完整 URL(跳过自动构造)

    返回: parse_index_valuation_percentile_rows 的输出(按日期升序的列表)
    """
    source_url = url.strip() if url else build_index_valuation_percentile_url(index_code)
    payload = fetch_json(source_url)
    result = parse_index_valuation_percentile_rows(payload, fallback_index_code=index_code)
    return result


# ---------------------------------------------------------------------------
# 10Y 国债收益率: 解析 + 取数
# ---------------------------------------------------------------------------

# 东方财富数据中心 - 中美国债收益率接口
# akshare bond_zh_us_rate 底层用的就是这个,字段映射:
#   SOLAR_DATE    -> 日期
#   EMM00166466   -> 中国国债收益率10年
#   EMM00588704   -> 中国国债收益率2年
#   EMM00166462   -> 中国国债收益率5年
#   EMM00166469   -> 中国国债收益率30年
_EASTMONEY_BOND_URL = "https://datacenter.eastmoney.com/api/data/get"
_EASTMONEY_BOND_TOKEN = "894050c76af8597a853f5b408b759f5d"
_BOND_FIELD_10Y = "EMM00166466"
_BOND_FIELD_2Y = "EMM00588704"
_BOND_FIELD_5Y = "EMM00166462"
_BOND_FIELD_30Y = "EMM00166469"
_BOND_FIELD_DATE = "SOLAR_DATE"


def _parse_bond_yield_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """解析单行东财国债数据,返回 {date, yield_2y, yield_5y, yield_10y, yield_30y}。

    10Y 为 None 的行(盘前发布部分列)返回 None,与旧代码 dropna 行为一致。
    """
    raw_date = row.get(_BOND_FIELD_DATE)
    trade_date = parse_optional_date(raw_date)
    yield_10y = parse_float(row.get(_BOND_FIELD_10Y))
    if trade_date is None or yield_10y is None:
        return None
    return {
        "date": trade_date,
        "yield_10y": yield_10y,
        "yield_2y": parse_float(row.get(_BOND_FIELD_2Y)),
        "yield_5y": parse_float(row.get(_BOND_FIELD_5Y)),
        "yield_30y": parse_float(row.get(_BOND_FIELD_30Y)),
    }


def parse_cn_10y_bond_yield_response(payload: object) -> list[dict[str, Any]]:
    """解析东财国债收益率接口返回的 JSON,返回全部历史行。

    输出: 按日期升序排列的列表,每项:
    {
        "trade_date": "2026-09-03",
        "cn_10y_bond_yield": 1.6798,
        "cn_2y_bond_yield": 1.2359,
        "cn_5y_bond_yield": 1.3939,
        "cn_30y_bond_yield": 2.132,
        "cn_10y_2y_spread": 0.4439,
    }
    """
    if not isinstance(payload, dict):
        raise ValueError("国债收益率接口返回格式异常(非 dict)")

    result_data = payload.get("result")
    if not isinstance(result_data, dict):
        raise ValueError("国债收益率接口缺少 result 字段")

    rows = result_data.get("data")
    if not isinstance(rows, list) or not rows:
        raise ValueError("国债收益率接口未返回有效数据")

    records: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed = _parse_bond_yield_row(row)
        if parsed is not None:
            records.append({
                "trade_date": parsed["date"].isoformat(),
                "cn_10y_bond_yield": parsed["yield_10y"],
                "cn_2y_bond_yield": parsed["yield_2y"],
                "cn_5y_bond_yield": parsed["yield_5y"],
                "cn_30y_bond_yield": parsed["yield_30y"],
                "cn_10y_2y_spread": round(parsed["yield_10y"] - parsed["yield_2y"], 4)
                    if parsed["yield_2y"] is not None else None,
            })

    if not records:
        raise ValueError("国债收益率接口数据均为空(10Y 列全为 None)")

    records.sort(key=lambda r: r["trade_date"])
    return records


def fetch_cn_10y_bond_yield() -> list[dict[str, Any]]:
    """抓取中国国债收益率历史数据(东方财富数据中心)。

    返回全部历史行(按日期升序),包含 2Y/5Y/10Y/30Y 及 10Y-2Y 期限利差。
    """
    params = {
        "type": "RPTA_WEB_TREASURYYIELD",
        "sty": "ALL",
        "st": "SOLAR_DATE",
        "sr": "-1",
        "token": _EASTMONEY_BOND_TOKEN,
        "p": "1",
        "ps": "5000",  # 拉全部历史
        "pageNo": "1",
        "pageNum": "1",
    }
    headers = {
        **DEFAULT_HEADERS,
        "Referer": "https://data.eastmoney.com/",
    }
    resp = httpx.get(_EASTMONEY_BOND_URL, params=params, headers=headers, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    result = parse_cn_10y_bond_yield_response(payload)
    return result
