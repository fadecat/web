# -*- coding: utf-8 -*-
"""股票高股息快照查询服务。

与 snapshots.py(转债快照)同构: latest 语义是「某交易日全量快照」。
排序默认股息率降序(对齐集思录股息率排行页), null 沉底, stock_id 兜底稳定。

序列化排除三列: raw_json(体积, ~1.1KB/行)、owned/holded(抓取账号的
私有自选/持仓态, 展示会误导)。其余 48 字段中的 46 个 + trade_date 全量输出。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.jisilu_stock import StockDividendDaily


def _to_dict(r: StockDividendDaily) -> dict[str, Any]:
    return {
        "trade_date": r.trade_date.isoformat() if r.trade_date else None,
        "stock_id": r.stock_id,
        "stock_nm": r.stock_nm,
        # 行业/地域
        "sw_cd": r.sw_cd,
        "industry": r.industry,
        "industry2": r.industry2,
        "industry_nm": r.industry_nm,
        "industry_nm2": r.industry_nm2,
        "province": r.province,
        # 行情
        "price": r.price,
        "pre_close": r.pre_close,
        "increase_rt": r.increase_rt,
        "volume": r.volume,
        "adj_rt": r.adj_rt,
        "price_5year": r.price_5year,
        # 规模
        "total_value": r.total_value,
        "float_value": r.float_value,
        "shares": r.shares,
        # 估值
        "pe": r.pe,
        "pb": r.pb,
        "roe": r.roe,
        "roe_average": r.roe_average,
        "pe_temperature": r.pe_temperature,
        "pb_temperature": r.pb_temperature,
        # 股息
        "dividend_rate": r.dividend_rate,
        "dividend_rate2": r.dividend_rate2,
        "dividend_rate5": r.dividend_rate5,
        "dividend_rate_average": r.dividend_rate_average,
        "dividend_rate_base": r.dividend_rate_base,
        "accu_dividend": r.accu_dividend,
        "aft_dividend": r.aft_dividend,
        # 财务质量
        "debt_rate": r.debt_rate,
        "int_debt_rate": r.int_debt_rate,
        "pledge_rt": r.pledge_rt,
        "eps_growth": r.eps_growth,
        "eps_growth_ttm": r.eps_growth_ttm,
        "revenue_average": r.revenue_average,
        "profit_average": r.profit_average,
        "cashflow_average": r.cashflow_average,
        # 元数据/标志
        "ipo_date": r.ipo_date,
        "last_dt": r.last_dt,
        "last_time": r.last_time,
        "audit_info": r.audit_info,
        "active_flg": r.active_flg,
        "margin_flg": r.margin_flg,
        "pb_flag": r.pb_flag,
        "stdevry": r.stdevry,
    }


def _latest_stmt(trade_date: str):
    """某交易日的全量快照查询(排序: 股息率降序 + null 沉底 + stock_id 兜底)。

    null 沉底用「IS NULL」布尔升序表达而非 NULLS LAST —— 后者需 SQLite ≥3.30,
    ECS 系统库 3.26 会报 near "NULLS" 语法错(生产已踩); 契约测试会编译本语句
    锁定可移植写法。
    """
    return (
        select(StockDividendDaily)
        .where(StockDividendDaily.trade_date == trade_date)
        .order_by(
            StockDividendDaily.dividend_rate.is_(None).asc(),
            StockDividendDaily.dividend_rate.desc(),
            StockDividendDaily.stock_id.asc(),
        )
    )


def get_latest(db: Session, trade_date: str | None = None) -> list[dict[str, Any]]:
    """某交易日全量高股息快照(默认最新交易日), 按股息率降序、null 沉底。"""
    if not trade_date:
        latest = db.scalar(select(func.max(StockDividendDaily.trade_date)))
        if not latest:
            return []
        trade_date = latest.isoformat()

    rows = db.scalars(_latest_stmt(trade_date)).all()
    return [_to_dict(r) for r in rows]
