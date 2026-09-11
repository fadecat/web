# -*- coding: utf-8 -*-
"""股票高股息快照存储层。

每天每只达标股票一行, 按日追加落库; 同 trade_date 重跑时对该日期
先删后插保证幂等, 不触碰其他日期(历史全量保存)。
对齐 cb_list_store.py 模式(数值列防御解析, raw_json 保留全 cell)。
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from backend.models.jisilu_stock import StockDividendDaily
from backend.utils import parse_float


def _parse_int(value: object) -> int | None:
    """整数列防御解析(owned/holded): 非数值 → None。"""
    parsed = parse_float(value)
    return int(parsed) if parsed is not None else None


def _clean_str(value: object) -> str | None:
    """字符串列清洗: 去空白, 空值 → None。"""
    text = str(value or "").strip()
    return text or None


def _row_from_cell(cell: dict[str, Any], trade_date: date) -> StockDividendDaily:
    """把集思录原始 cell 映射为快照行(48 字段全量 + raw_json)。"""
    return StockDividendDaily(
        trade_date=trade_date,
        stock_id=str(cell.get("stock_id") or "").strip(),
        stock_nm=_clean_str(cell.get("stock_nm")),
        # 行业/地域
        sw_cd=_clean_str(cell.get("sw_cd")),
        industry=_clean_str(cell.get("industry")),
        industry2=_clean_str(cell.get("industry2")),
        industry_nm=_clean_str(cell.get("industry_nm")),
        industry_nm2=_clean_str(cell.get("industry_nm2")),
        province=_clean_str(cell.get("province")),
        # 行情
        price=parse_float(cell.get("price")),
        pre_close=parse_float(cell.get("pre_close")),
        increase_rt=parse_float(cell.get("increase_rt")),
        volume=parse_float(cell.get("volume")),
        adj_rt=parse_float(cell.get("adj_rt")),
        price_5year=parse_float(cell.get("price_5year")),
        # 规模
        total_value=parse_float(cell.get("total_value")),
        float_value=parse_float(cell.get("float_value")),
        shares=parse_float(cell.get("shares")),
        # 估值
        pe=parse_float(cell.get("pe")),
        pb=parse_float(cell.get("pb")),
        roe=parse_float(cell.get("roe")),
        roe_average=parse_float(cell.get("roe_average")),
        pe_temperature=parse_float(cell.get("pe_temperature")),
        pb_temperature=parse_float(cell.get("pb_temperature")),
        # 股息
        dividend_rate=parse_float(cell.get("dividend_rate")),
        dividend_rate2=parse_float(cell.get("dividend_rate2")),
        dividend_rate5=parse_float(cell.get("dividend_rate5")),
        dividend_rate_average=parse_float(cell.get("dividend_rate_average")),
        dividend_rate_base=parse_float(cell.get("dividend_rate_base")),
        accu_dividend=parse_float(cell.get("accu_dividend")),
        aft_dividend=parse_float(cell.get("aft_dividend")),
        # 财务质量(pledge_rt 偶为 'buy' 徽标串 → None)
        debt_rate=parse_float(cell.get("debt_rate")),
        int_debt_rate=parse_float(cell.get("int_debt_rate")),
        pledge_rt=parse_float(cell.get("pledge_rt")),
        eps_growth=parse_float(cell.get("eps_growth")),
        eps_growth_ttm=parse_float(cell.get("eps_growth_ttm")),
        revenue_average=parse_float(cell.get("revenue_average")),
        profit_average=parse_float(cell.get("profit_average")),
        cashflow_average=parse_float(cell.get("cashflow_average")),
        # 元数据/标志(stdevry 存原样, 徽标串不转 None)
        ipo_date=_clean_str(cell.get("ipo_date")),
        last_dt=_clean_str(cell.get("last_dt")),
        last_time=_clean_str(cell.get("last_time")),
        audit_info=_clean_str(cell.get("audit_info")),
        active_flg=_clean_str(cell.get("active_flg")),
        margin_flg=_clean_str(cell.get("margin_flg")),
        pb_flag=_clean_str(cell.get("pb_flag")),
        stdevry=_clean_str(cell.get("stdevry")),
        # 账号自选态
        owned=_parse_int(cell.get("owned")),
        holded=_parse_int(cell.get("holded")),
        # 兜底
        raw_json=json.dumps(cell, ensure_ascii=False),
    )


def save_stock_dividend_snapshot(
    db: Session,
    cells: list[dict[str, Any]],
    trade_date: date,
) -> int:
    """批量写入高股息股票日频快照(同 trade_date 先删后插幂等)。

    参数:
        db: SQLAlchemy 会话
        cells: fetch_dividend_snapshot 返回的 rows(集思录原始 cell)
        trade_date: 交易日

    返回: 新写入行数(无 stock_id 的脏行跳过)。

    幂等策略: 仅当写入该日期时先删除该日旧行再整批插入 —— 手动/定时重跑
    同一天以最新抓取为准; 其他日期的历史行不受影响。
    """
    valid = [
        cell for cell in cells
        if str(cell.get("stock_id") or "").strip()
    ]
    db.query(StockDividendDaily).filter(
        StockDividendDaily.trade_date == trade_date
    ).delete(synchronize_session=False)
    for cell in valid:
        db.add(_row_from_cell(cell, trade_date))
    db.commit()
    return len(valid)
