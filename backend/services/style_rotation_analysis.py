# -*- coding: utf-8 -*-
"""风格轮动分析算法。

基于两指数日线收盘价计算:
- spread: 各自 N 日收益率差值(左 - 右),衡量「左侧强/右侧弱」幅度
- ma: spread 的 MA20 趋势线
- p90/p10: spread 全局 90/10 分位阈值(全局历史阈值, 不滚动)
- p90_dynamic/p10_dynamic: 滚动 90/10 分位阈值(从起点 expanding 累积)

依赖: pandas, numpy。
数据源: backend.models.valuation.IndexDailyQuote。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.valuation import IndexDailyQuote


class InsufficientDataError(ValueError):
    """数据不足以计算时抛出。"""


@dataclass(frozen=True)
class StyleRotationParams:
    left_symbol: str
    right_symbol: str
    start_date: str | None
    end_date: str | None
    return_window: int = 20
    max_window: int = 20
    quantile_window_min: int = 20


def _load_price_frame(
    db: Session,
    index_code: str,
    query_start: date | None,
    query_end: date | None,
) -> pd.DataFrame:
    """加载单只指数的日线 close 列,转成 DataFrame。"""
    stmt = select(
        IndexDailyQuote.trade_date,
        IndexDailyQuote.close,
    ).where(IndexDailyQuote.index_code == index_code)
    if query_start:
        stmt = stmt.where(IndexDailyQuote.trade_date >= query_start)
    if query_end:
        stmt = stmt.where(IndexDailyQuote.trade_date <= query_end)
    stmt = stmt.order_by(IndexDailyQuote.trade_date.asc())

    rows = db.execute(stmt).all()
    if not rows:
        return pd.DataFrame(columns=["trade_date", "close"])

    return pd.DataFrame(rows, columns=["trade_date", "close"])


def calculate_style_rotation(
    df_left: pd.DataFrame,
    df_right: pd.DataFrame,
    params: StyleRotationParams,
) -> dict[str, Any]:
    """核心计算: 输入两指数日线, 输出 spread/ma/p90/p10 等序列。"""
    df_left = df_left.copy()
    df_right = df_right.copy()

    df_left["trade_date"] = pd.to_datetime(df_left["trade_date"])
    df_right["trade_date"] = pd.to_datetime(df_right["trade_date"])

    df_left["close"] = df_left["close"].astype(float)
    df_right["close"] = df_right["close"].astype(float)

    df_left = df_left.sort_values("trade_date").reset_index(drop=True)
    df_right = df_right.sort_values("trade_date").reset_index(drop=True)

    # 内连接对齐交易日
    df = pd.merge(
        df_left[["trade_date", "close"]],
        df_right[["trade_date", "close"]],
        on="trade_date",
        how="inner",
        suffixes=("_left", "_right"),
    )

    if df.empty:
        raise InsufficientDataError("aligned data is empty")

    df = df.sort_values("trade_date").reset_index(drop=True)

    # N 日收益率(%)
    df["left_return"] = df["close_left"].pct_change(params.return_window) * 100
    df["right_return"] = df["close_right"].pct_change(params.return_window) * 100
    df["spread"] = df["left_return"] - df["right_return"]
    df = df.dropna(subset=["left_return", "right_return", "spread"]).reset_index(drop=True)

    if df.empty:
        raise InsufficientDataError("not enough data after return window")

    # MA 趋势线
    df["ma"] = df["spread"].rolling(params.max_window).mean()

    # 滚动分位阈值(expanding 累积, 前段 quantile_window_min 天不计算)
    df["p90_dynamic"] = (
        df["spread"].expanding(min_periods=params.quantile_window_min).quantile(0.9)
    )
    df["p10_dynamic"] = (
        df["spread"].expanding(min_periods=params.quantile_window_min).quantile(0.1)
    )

    df["date_str"] = df["trade_date"].dt.strftime("%Y-%m-%d")

    # 全局阈值(整个历史区间的 90/10 分位)
    global_p90 = round(float(df["spread"].quantile(0.9)), 2)
    global_p10 = round(float(df["spread"].quantile(0.1)), 2)

    # 日期范围裁剪
    if params.start_date:
        df = df[df["date_str"] >= params.start_date].reset_index(drop=True)
    if params.end_date:
        df = df[df["date_str"] <= params.end_date].reset_index(drop=True)

    df = df.dropna(subset=["ma", "p90_dynamic", "p10_dynamic"]).reset_index(drop=True)

    if df.empty:
        raise InsufficientDataError("empty after date filter")

    return {
        "dates": df["date_str"].tolist(),
        "spread": [round(float(v), 2) for v in df["spread"].tolist()],
        "ma": [round(float(v), 2) for v in df["ma"].tolist()],
        "p90_dynamic": [round(float(v), 2) for v in df["p90_dynamic"].tolist()],
        "p10_dynamic": [round(float(v), 2) for v in df["p10_dynamic"].tolist()],
        "global_p90": global_p90,
        "global_p10": global_p10,
        "latest_spread": round(float(df["spread"].iloc[-1]), 2),
        "latest_ma": round(float(df["ma"].iloc[-1]), 2),
        "latest_date": df["date_str"].iloc[-1],
    }


def build_style_rotation_response(
    db: Session,
    params: StyleRotationParams,
) -> dict[str, Any]:
    """路由层入口: 加载数据 + 计算 + 包装 meta/series/summary。"""
    query_start = None
    if params.start_date:
        query_start = date.fromisoformat(params.start_date) - timedelta(
            days=max(400, params.return_window + 150)
        )
    query_end = date.fromisoformat(params.end_date) if params.end_date else None

    df_left = _load_price_frame(db, params.left_symbol, query_start, query_end)
    df_right = _load_price_frame(db, params.right_symbol, query_start, query_end)

    if df_left.empty or df_right.empty:
        raise InsufficientDataError(
            f"symbol data missing: left={len(df_left)}, right={len(df_right)}"
        )

    result = calculate_style_rotation(df_left, df_right, params)

    return {
        "meta": {
            "left_symbol": params.left_symbol,
            "right_symbol": params.right_symbol,
            "return_window": params.return_window,
            "max_window": params.max_window,
            "start_date": params.start_date,
            "end_date": params.end_date,
        },
        "series": {
            "dates": result["dates"],
            "spread": result["spread"],
            "ma": result["ma"],
            "p90_dynamic": result["p90_dynamic"],
            "p10_dynamic": result["p10_dynamic"],
        },
        "summary": {
            "latest_spread": result["latest_spread"],
            "latest_ma": result["latest_ma"],
            "latest_date": result["latest_date"],
            "global_p90": result["global_p90"],
            "global_p10": result["global_p10"],
        },
    }