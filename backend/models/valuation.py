# -*- coding: utf-8 -*-
"""市场估值板块 ORM 模型。

设计要点(见 docs/web-refactor.md):
- 宽表: 一次快照一行,字段对齐易方达源数据返回结构,便于全量保存。
- 指数估值快照(IndexValuationSnapshot): 每股指数每日收盘后的 PE/PB/PS 及分位。
- 股息率(IndexDividendYield): 独立来源,单列成表。
- 全量保存: 不覆盖历史,追加式落库,支持回溯任意交易日。

分位周期对齐易方达源数据(9 个):
  3M / 6M / 1Y / 2Y / 3Y / 5Y / 10Y / 今年以来 / 成立以来
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.database import Base


class IndexValuationSnapshot(Base):
    """指数估值日频快照(宽表)。

    一行 = 一只指数在某一个交易日的完整估值截面。
    PE/PB/PS 各自带 9 个周期分位,字段对齐 INDEX_VALUATION_METRIC_FIELDS。
    """

    __tablename__ = "index_valuation_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 指数标识
    index_code: Mapped[str] = mapped_column(String(16), nullable=False, comment="指数代码")
    index_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="指数名称")

    # 快照时间
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="落库时间")

    # 估值指标(PE/PB/PS 当前值)
    pe: Mapped[float | None] = mapped_column(Float, nullable=True, comment="PE-TTM")
    pb: Mapped[float | None] = mapped_column(Float, nullable=True, comment="PB")
    ps: Mapped[float | None] = mapped_column(Float, nullable=True, comment="PS")

    # PE 分位(9 个周期, 对齐源数据)
    pe_percentile_3m: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_percentile_6m: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_percentile_1y: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_percentile_2y: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_percentile_3y: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_percentile_5y: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_percentile_10y: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_percentile_ytd: Mapped[float | None] = mapped_column(Float, nullable=True, comment="今年以来")
    pe_percentile_bgn: Mapped[float | None] = mapped_column(Float, nullable=True, comment="成立以来")

    # PB 分位
    pb_percentile_3m: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb_percentile_6m: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb_percentile_1y: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb_percentile_2y: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb_percentile_3y: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb_percentile_5y: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb_percentile_10y: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb_percentile_ytd: Mapped[float | None] = mapped_column(Float, nullable=True, comment="今年以来")
    pb_percentile_bgn: Mapped[float | None] = mapped_column(Float, nullable=True, comment="成立以来")

    # PS 分位
    ps_percentile_3m: Mapped[float | None] = mapped_column(Float, nullable=True)
    ps_percentile_6m: Mapped[float | None] = mapped_column(Float, nullable=True)
    ps_percentile_1y: Mapped[float | None] = mapped_column(Float, nullable=True)
    ps_percentile_2y: Mapped[float | None] = mapped_column(Float, nullable=True)
    ps_percentile_3y: Mapped[float | None] = mapped_column(Float, nullable=True)
    ps_percentile_5y: Mapped[float | None] = mapped_column(Float, nullable=True)
    ps_percentile_10y: Mapped[float | None] = mapped_column(Float, nullable=True)
    ps_percentile_ytd: Mapped[float | None] = mapped_column(Float, nullable=True, comment="今年以来")
    ps_percentile_bgn: Mapped[float | None] = mapped_column(Float, nullable=True, comment="成立以来")

    __table_args__ = (
        UniqueConstraint("index_code", "trade_date", name="uq_valuation_idx_date"),
        Index("ix_valuation_idx_date", "index_code", "trade_date"),
    )


class IndexDividendYield(Base):
    """指数股息率(独立来源,日频追加)。

    含最新股息率 + 1Y/3Y/5Y/10Y 分位 + 5Y 均值。
    """

    __tablename__ = "index_dividend_yield"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    index_code: Mapped[str] = mapped_column(String(16), nullable=False, comment="指数代码")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日")
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True, comment="股息率(%)")

    # 分位(4 个周期)
    dividend_yield_percentile_1y: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield_percentile_3y: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield_percentile_5y: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield_percentile_10y: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 5Y 均值
    dividend_yield_average_5y: Mapped[float | None] = mapped_column(Float, nullable=True, comment="5年均值")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="落库时间")

    __table_args__ = (
        UniqueConstraint("index_code", "trade_date", name="uq_dividend_idx_date"),
        Index("ix_dividend_idx_date", "index_code", "trade_date"),
    )


class CnBondYield(Base):
    """中国国债收益率(日频追加)。

    含 2Y/5Y/10Y/30Y 及 10Y-2Y 期限利差。
    """

    __tablename__ = "cn_bond_yield"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日")

    yield_2y: Mapped[float | None] = mapped_column(Float, nullable=True, comment="2年期(%)")
    yield_5y: Mapped[float | None] = mapped_column(Float, nullable=True, comment="5年期(%)")
    yield_10y: Mapped[float | None] = mapped_column(Float, nullable=True, comment="10年期(%)")
    yield_30y: Mapped[float | None] = mapped_column(Float, nullable=True, comment="30年期(%)")
    spread_10y_2y: Mapped[float | None] = mapped_column(Float, nullable=True, comment="10Y-2Y期限利差")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="落库时间")

    __table_args__ = (
        UniqueConstraint("trade_date", name="uq_bond_date"),
        Index("ix_bond_date", "trade_date"),
    )


class IndexDailyQuote(Base):
    """指数日线行情原始数据(日频追加)。

    全量保存 OHLCV,不做计算,与市场估值板块的快照表同一设计理念。
    风格轮动用 399376(国证小盘成长) 和 399373(国证大盘价值)。
    后续其他板块需要指数日线也可复用此表。
    """

    __tablename__ = "index_daily_quote"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    index_code: Mapped[str] = mapped_column(String(16), nullable=False, comment="指数代码")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="落库时间")

    # OHLCV 原始字段
    open: Mapped[float | None] = mapped_column(Float, nullable=True, comment="开盘价")
    close: Mapped[float | None] = mapped_column(Float, nullable=True, comment="收盘价")
    high: Mapped[float | None] = mapped_column(Float, nullable=True, comment="最高价")
    low: Mapped[float | None] = mapped_column(Float, nullable=True, comment="最低价")
    volume: Mapped[float | None] = mapped_column(Float, nullable=True, comment="成交量")

    __table_args__ = (
        UniqueConstraint("index_code", "trade_date", name="uq_quote_idx_date"),
        Index("ix_quote_idx_date", "index_code", "trade_date"),
    )


class CbIndexDaily(Base):
    """可转债等权指数日频数据(原始字段全量保存)。

    数据源: 集思录 cb_index 页面。
    字段对齐 JISILU_FIELD_MAP,不做计算,追加式落库。
    """

    __tablename__ = "cb_index_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="落库时间")

    # 核心指标
    index_value: Mapped[float | None] = mapped_column(Float, nullable=True, comment="等权指数值")
    median_price: Mapped[float | None] = mapped_column(Float, nullable=True, comment="价格中位数")
    avg_price: Mapped[float | None] = mapped_column(Float, nullable=True, comment="平均价格")
    avg_ytm: Mapped[float | None] = mapped_column(Float, nullable=True, comment="平均到期收益率(%)")
    median_convert_value: Mapped[float | None] = mapped_column(Float, nullable=True, comment="中位数转股价值")
    avg_dblow: Mapped[float | None] = mapped_column(Float, nullable=True, comment="平均双低")
    avg_premium: Mapped[float | None] = mapped_column(Float, nullable=True, comment="平均溢价率(%)")
    median_premium: Mapped[float | None] = mapped_column(Float, nullable=True, comment="中位数溢价率(%)")
    turnover_rate: Mapped[float | None] = mapped_column(Float, nullable=True, comment="换手率(%)")
    count: Mapped[float | None] = mapped_column(Float, nullable=True, comment="转债数量")
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True, comment="温度")
    idx_price: Mapped[float | None] = mapped_column(Float, nullable=True, comment="指数价格")
    idx_increase_rt: Mapped[float | None] = mapped_column(Float, nullable=True, comment="指数涨幅(%)")

    __table_args__ = (
        UniqueConstraint("trade_date", name="uq_cb_index_date"),
        Index("ix_cb_index_date", "trade_date"),
    )


class CbDailySnapshot(Base):
    """可转债每日全量快照(每只转债每天一行)。

    数据源: 集思录 cb_list_new (全量已上市转债)。
    设计: 原始字段全量保存,不做过滤/计算。后续三低等策略基于此数据做。
    唯一约束 (bond_id, trade_date) 保证幂等。
    """

    __tablename__ = "cb_daily_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="落库时间")

    # 转债标识
    bond_id: Mapped[str] = mapped_column(String(16), nullable=False, comment="转债代码")
    bond_nm: Mapped[str] = mapped_column(String(64), nullable=True, comment="转债名称")

    # 正股
    stock_id: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="正股代码")
    stock_nm: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="正股名称")

    # 价格
    price: Mapped[float | None] = mapped_column(Float, nullable=True, comment="转债价格")
    sprice: Mapped[float | None] = mapped_column(Float, nullable=True, comment="正股价格")
    increase_rt: Mapped[float | None] = mapped_column(Float, nullable=True, comment="转债涨跌幅(%)")
    sincrease_rt: Mapped[float | None] = mapped_column(Float, nullable=True, comment="正股涨跌幅(%)")

    # 转股
    convert_price: Mapped[float | None] = mapped_column(Float, nullable=True, comment="转股价")
    convert_value: Mapped[float | None] = mapped_column(Float, nullable=True, comment="转股价值")
    premium_rt: Mapped[float | None] = mapped_column(Float, nullable=True, comment="溢价率(%)")
    dblow: Mapped[float | None] = mapped_column(Float, nullable=True, comment="双低值")

    # 规模与期限
    curr_iss_amt: Mapped[float | None] = mapped_column(Float, nullable=True, comment="剩余规模(亿)")
    orig_iss_amt: Mapped[float | None] = mapped_column(Float, nullable=True, comment="原始规模(亿)")
    year_left: Mapped[float | None] = mapped_column(Float, nullable=True, comment="剩余年限")
    maturity_dt: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="到期日")
    list_dt: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="上市日")

    # 评级与指标
    rating_cd: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="评级")
    ytm_rt: Mapped[float | None] = mapped_column(Float, nullable=True, comment="到期税前收益率(%)")
    put_ytm_rt: Mapped[float | None] = mapped_column(Float, nullable=True, comment="回售收益率(%)")
    pb: Mapped[float | None] = mapped_column(Float, nullable=True, comment="正股PB")
    turnover_rt: Mapped[float | None] = mapped_column(Float, nullable=True, comment="换手率(%)")
    volume: Mapped[float | None] = mapped_column(Float, nullable=True, comment="成交量")
    svolume: Mapped[float | None] = mapped_column(Float, nullable=True, comment="正股成交量")

    # 强赎/回售
    force_redeem_price: Mapped[float | None] = mapped_column(Float, nullable=True, comment="强赎触发价")
    put_convert_price: Mapped[float | None] = mapped_column(Float, nullable=True, comment="回售触发价")
    convert_amt_ratio: Mapped[float | None] = mapped_column(Float, nullable=True, comment="转股比例(%)")

    # 市场/行业
    market_cd: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="市场代码")
    sw_cd: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="申万行业代码")
    btype: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="债券类型")

    # 原始 JSON(存全部 64 字段,后续需要时可从这里取)
    raw_json: Mapped[str | None] = mapped_column(String, nullable=True, comment="原始 cell JSON")

    __table_args__ = (
        UniqueConstraint("bond_id", "trade_date", name="uq_cb_snapshot_bond_date"),
        Index("ix_cb_snapshot_date", "trade_date"),
        Index("ix_cb_snapshot_bond", "bond_id"),
    )
