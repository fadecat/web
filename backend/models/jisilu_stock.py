# -*- coding: utf-8 -*-
"""集思录股票板块 ORM 模型。

设计要点(见 docs/superpowers/plans/2026-09-11-stock-dividend-module-design.md §4):
- 宽表对齐 CbDailySnapshot 模式: 集思录返回的 48 个 cell 字段全部建结构化列,
  不做裁剪; raw_json 仍保留完整 cell, 源站未来新增字段自动进 raw_json。
- 追加式全量保存, (stock_id, trade_date) 唯一约束保证幂等。
- 数据瑕疵防御: pledge_rt/stdevry 实测偶为 'buy' 徽标串 —— pledge_rt 建数值列,
  解析时非数值转 None; stdevry 直接建 String 列保留原样。
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.database import Base


class StockDividendDaily(Base):
    """股票高股息日频快照(每只 ≥200亿 股票每天一行)。

    数据源: 集思录 dividend_rate_list(股息率排行页接口)。
    快照成员随市值穿越门槛边界进出, 历史查询是「当时成员」口径。
    """

    __tablename__ = "stock_dividend_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="落库时间")

    # 股票标识
    stock_id: Mapped[str] = mapped_column(String(16), nullable=False, comment="股票代码")
    stock_nm: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="股票名称")

    # ---- 集思录 48 字段全量建列(分组注释, 类型按实测样本) ----

    # 行业/地域(industry/industry2 实测恒空, 仍建列对齐源结构)
    sw_cd: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="申万行业代码")
    industry: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="行业(实测恒空)")
    industry2: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="行业2(实测恒空)")
    industry_nm: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="三级行业名")
    industry_nm2: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="行业路径名")
    province: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="省份")

    # 行情
    price: Mapped[float | None] = mapped_column(Float, nullable=True, comment="现价")
    pre_close: Mapped[float | None] = mapped_column(Float, nullable=True, comment="昨收价")
    increase_rt: Mapped[float | None] = mapped_column(Float, nullable=True, comment="涨跌幅(%)")
    volume: Mapped[float | None] = mapped_column(Float, nullable=True, comment="成交量(手)")
    adj_rt: Mapped[float | None] = mapped_column(Float, nullable=True, comment="复权因子")
    price_5year: Mapped[float | None] = mapped_column(Float, nullable=True, comment="5年均价")

    # 规模
    total_value: Mapped[float | None] = mapped_column(Float, nullable=True, comment="总市值(亿)")
    float_value: Mapped[float | None] = mapped_column(Float, nullable=True, comment="流通市值(亿)")
    shares: Mapped[float | None] = mapped_column(Float, nullable=True, comment="总股本(亿股)")

    # 估值
    pe: Mapped[float | None] = mapped_column(Float, nullable=True, comment="PE-TTM")
    pb: Mapped[float | None] = mapped_column(Float, nullable=True, comment="PB")
    roe: Mapped[float | None] = mapped_column(Float, nullable=True, comment="ROE(%)")
    roe_average: Mapped[float | None] = mapped_column(Float, nullable=True, comment="ROE均值(%)")
    pe_temperature: Mapped[float | None] = mapped_column(Float, nullable=True, comment="PE温度")
    pb_temperature: Mapped[float | None] = mapped_column(Float, nullable=True, comment="PB温度")

    # 股息
    dividend_rate: Mapped[float | None] = mapped_column(Float, nullable=True, comment="股息率(%)")
    dividend_rate2: Mapped[float | None] = mapped_column(Float, nullable=True, comment="股息率2(%)")
    dividend_rate5: Mapped[float | None] = mapped_column(Float, nullable=True, comment="5年股息率(%)")
    dividend_rate_average: Mapped[float | None] = mapped_column(Float, nullable=True, comment="股息率均值(%)")
    dividend_rate_base: Mapped[float | None] = mapped_column(Float, nullable=True, comment="基础股息率(%)")
    accu_dividend: Mapped[float | None] = mapped_column(Float, nullable=True, comment="累计股息(元)")
    aft_dividend: Mapped[float | None] = mapped_column(Float, nullable=True, comment="除权股息率(%)")

    # 财务质量(pledge_rt 偶为 'buy' 徽标串, 解析防御转 None)
    debt_rate: Mapped[float | None] = mapped_column(Float, nullable=True, comment="资产负债率(%)")
    int_debt_rate: Mapped[float | None] = mapped_column(Float, nullable=True, comment="有息负债率(%)")
    pledge_rt: Mapped[float | None] = mapped_column(Float, nullable=True, comment="质押比例(%)")
    eps_growth: Mapped[float | None] = mapped_column(Float, nullable=True, comment="EPS增长(%)")
    eps_growth_ttm: Mapped[float | None] = mapped_column(Float, nullable=True, comment="EPS增长TTM(%)")
    revenue_average: Mapped[float | None] = mapped_column(Float, nullable=True, comment="营收均值(%)")
    profit_average: Mapped[float | None] = mapped_column(Float, nullable=True, comment="利润均值(%)")
    cashflow_average: Mapped[float | None] = mapped_column(Float, nullable=True, comment="现金流均值(%)")

    # 元数据/标志(stdevry 偶为 'buy' 徽标串, 建 String 列保留原样)
    ipo_date: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="上市日期")
    last_dt: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="行情日期(停牌股较旧)")
    last_time: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="行情时间")
    audit_info: Mapped[str | None] = mapped_column(String, nullable=True, comment="审计信息")
    active_flg: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="活跃标志")
    margin_flg: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="两融标志")
    pb_flag: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="PB标志")
    stdevry: Mapped[str | None] = mapped_column(String, nullable=True, comment="波动率(实测偶为徽标串, 存原样)")

    # 账号自选态(登录态下为账号维度, 实现按现值存)
    owned: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="自选标志")
    holded: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="持仓标志")

    # 兜底: 完整原始 cell(源站新增字段自动进这里)
    raw_json: Mapped[str | None] = mapped_column(String, nullable=True, comment="原始 cell JSON")

    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", name="uq_stock_dividend_id_date"),
        Index("ix_stock_dividend_date", "trade_date"),
        Index("ix_stock_dividend_sw_cd", "sw_cd"),
    )
