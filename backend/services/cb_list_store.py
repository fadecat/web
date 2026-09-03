# -*- coding: utf-8 -*-
"""可转债全量快照存储层。

每天每只转债一行,幂等追加。
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from backend.models.valuation import CbDailySnapshot
from backend.utils import parse_float


def save_cb_snapshots(
    db: Session,
    records: list[dict[str, Any]],
    trade_date: date,
) -> int:
    """批量写入可转债全量快照。

    参数:
        db: SQLAlchemy 会话
        records: fetch_cb_list 返回的 cell 列表
        trade_date: 交易日

    返回: 新写入行数(已存在的跳过)。
    """
    inserted = 0
    for cell in records:
        bond_id = str(cell.get("bond_id", "")).strip()
        if not bond_id:
            continue

        existing = db.query(CbDailySnapshot).filter_by(
            bond_id=bond_id,
            trade_date=trade_date,
        ).first()
        if existing:
            continue

        db.add(CbDailySnapshot(
            trade_date=trade_date,
            bond_id=bond_id,
            bond_nm=str(cell.get("bond_nm") or "").strip(),
            stock_id=str(cell.get("stock_id") or "").strip() or None,
            stock_nm=str(cell.get("stock_nm") or "").strip() or None,
            price=parse_float(cell.get("price")),
            sprice=parse_float(cell.get("sprice")),
            increase_rt=parse_float(cell.get("increase_rt")),
            sincrease_rt=parse_float(cell.get("sincrease_rt")),
            convert_price=parse_float(cell.get("convert_price")),
            convert_value=parse_float(cell.get("convert_value")),
            premium_rt=parse_float(cell.get("premium_rt")),
            dblow=parse_float(cell.get("dblow")),
            curr_iss_amt=parse_float(cell.get("curr_iss_amt")),
            orig_iss_amt=parse_float(cell.get("orig_iss_amt")),
            year_left=parse_float(cell.get("year_left")),
            maturity_dt=str(cell.get("maturity_dt") or "").strip() or None,
            list_dt=str(cell.get("list_dt") or "").strip() or None,
            rating_cd=str(cell.get("rating_cd") or "").strip() or None,
            ytm_rt=parse_float(cell.get("ytm_rt")),
            put_ytm_rt=parse_float(cell.get("put_ytm_rt")),
            pb=parse_float(cell.get("pb")),
            turnover_rt=parse_float(cell.get("turnover_rt")),
            volume=parse_float(cell.get("volume")),
            svolume=parse_float(cell.get("svolume")),
            force_redeem_price=parse_float(cell.get("force_redeem_price")),
            put_convert_price=parse_float(cell.get("put_convert_price")),
            convert_amt_ratio=parse_float(cell.get("convert_amt_ratio")),
            market_cd=str(cell.get("market_cd") or "").strip() or None,
            sw_cd=str(cell.get("sw_cd") or "").strip() or None,
            btype=str(cell.get("btype") or "").strip() or None,
            raw_json=json.dumps(cell, ensure_ascii=False),
        ))
        inserted += 1

    db.commit()
    return inserted
