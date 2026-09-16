# -*- coding: utf-8 -*-
"""数据管理页聚合服务: 按指数汇总各数据集的来源/最新日期/状态。

与 data_status.get_dataset_freshness 的区别:
  - 这里按「指数」聚合(一个指数一张卡片), 而非按「指标」分组;
  - 状态判定沿用 data_status 的 freshness 口径(fresh/stale/lagging/no_data),
    但绝不把「更新较晚」伪装成「抓取失败」——失败只来自任务运行记录。
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.valuation import (
    CbDailySnapshot,
    CbIndexDaily,
    CbRedeemDaily,
    CnBondYield,
    IndexDailyQuote,
    IndexDividendYield,
    IndexValuationSnapshot,
)
from backend.models.commodity import CommodityDailyPrice, CommodityInstrument, CommoditySyncState
from backend.services.data_status import (
    _expected_date,
    _freshness_state,
    get_job_runs,
)
from backend.services.data_catalog import Policy
from backend.services.index_universe import SOURCES, load_universe


def _dataset_state(latest: date | None, expected: date) -> str:
    return _freshness_state(latest, expected)


def build_data_management(db: Session, now: datetime | None = None) -> dict:
    """数据管理页完整数据: 指数列表(按指数聚合) + 非指数分组 + 任务 + 数据源。"""
    expected = _expected_date()
    commodity_expected, _commodity_next_due = Policy(
        "akshare", "commodity_daily", 15, 50
    ).expected(now)

    # ---- 预取各表的按 code 聚合统计(一次查询, 避免逐指数 N+1) ----
    def _agg_map(model, extra=None):
        stmt = select(
            model.index_code,
            func.max(model.trade_date),
            func.min(model.trade_date),
            func.count(),
        )
        if extra is not None:
            stmt = stmt.where(extra)
        stmt = stmt.group_by(model.index_code)
        out = {}
        for code, latest, first, count in db.execute(stmt).all():
            out[str(code)] = (latest, first, count)
        return out

    quote_agg = _agg_map(IndexDailyQuote)
    pe_agg = _agg_map(IndexValuationSnapshot, IndexValuationSnapshot.pe.isnot(None))
    pb_agg = _agg_map(IndexValuationSnapshot, IndexValuationSnapshot.pb.isnot(None))
    div_agg = _agg_map(IndexDividendYield)

    # 名称兜底: 估值表内的 index_name
    name_map = {
        str(code): name
        for code, name in db.execute(
            select(IndexValuationSnapshot.index_code, IndexValuationSnapshot.index_name).distinct()
        ).all()
    }

    def _ds_item(key, label, source, enabled, agg, storage):
        row = agg.get(storage)
        latest, first, count = row if row else (None, None, None)
        state = "disabled" if not enabled else _dataset_state(latest, expected)
        return {
            "key": key,
            "label": label,
            "source": source,
            "enabled": bool(enabled),
            "state": state,
            "latest_date": latest.isoformat() if latest else None,
            "first_date": first.isoformat() if first else None,
            "count": count,
        }

    indexes = []
    for idx in load_universe():
        code = idx.get("code", "")
        datasets_cfg = idx.get("datasets", {})
        name = idx.get("name") or name_map.get(code, code)

        ds_list = []
        # quote(日线)
        q = datasets_cfg.get("quote")
        if q:
            ds_list.append(_ds_item(
                "quote",
                "日K" if q.get("source") == "tencent" else "收盘价",
                q.get("source", ""),
                q.get("enabled", True),
                quote_agg,
                q.get("storage_code") or code,
            ))
        # valuation → 拆 pe / pb / dividend 三列
        v = datasets_cfg.get("valuation")
        if v:
            storage = v.get("storage_code") or code
            ds_list.append(_ds_item("pe", "PE", v.get("source", ""), v.get("enabled", True), pe_agg, storage))
            ds_list.append(_ds_item("pb", "PB", v.get("source", ""), v.get("enabled", True), pb_agg, storage))
            ds_list.append(_ds_item("dividend", "股息率", v.get("source", ""), v.get("enabled", True), div_agg, storage))

        indexes.append({
            "code": code,
            "name": name,
            "enabled": idx.get("enabled", True) is not False,
            "datasets": ds_list,
        })

    # ---- 非指数分组(国债 + 转债类) ----
    def _single(model, label, unit="天"):
        latest, first, days = db.execute(
            select(func.max(model.trade_date), func.min(model.trade_date), func.count(func.distinct(model.trade_date)))
        ).one()
        return {
            "label": label,
            "latest_date": latest.isoformat() if latest else None,
            "first_date": first.isoformat() if first else None,
            "count": days,
            "unit": unit,
            "state": _dataset_state(latest, expected),
        }

    non_index_groups = [
        _single(CnBondYield, "国债收益率(10Y/2Y/5Y/30Y)"),
        _single(CbDailySnapshot, "转债全量快照"),
        _single(CbRedeemDaily, "强赎列表"),
        _single(CbIndexDaily, "转债等权指数"),
    ]

    commodity_instruments = db.execute(
        select(CommodityInstrument).order_by(CommodityInstrument.display_order, CommodityInstrument.code)
    ).scalars().all()
    if commodity_instruments:
        price_rows = db.execute(
            select(
                CommodityDailyPrice.instrument_code,
                func.max(CommodityDailyPrice.trade_date),
                func.min(CommodityDailyPrice.trade_date),
                func.count(),
            ).group_by(CommodityDailyPrice.instrument_code)
        ).all()
        prices = {str(code): (latest, first, count) for code, latest, first, count in price_rows}
        states = {
            row.instrument_code: row
            for row in db.execute(select(CommoditySyncState)).scalars().all()
        }
        commodity_entities = []
        for instrument in commodity_instruments:
            latest, first, count = prices.get(instrument.code, (None, None, 0))
            state = (
                "disabled"
                if not instrument.enabled
                else _dataset_state(latest, commodity_expected)
            )
            commodity_entities.append({
                "label": f"{instrument.name} {instrument.code}",
                "instrument_code": instrument.code,
                "source": "akshare",
                "job_id": "commodity_daily",
                "schedule": "交易日 15:50",
                "enabled": bool(instrument.enabled),
                "state": state,
                "sync_status": states[instrument.code].status if instrument.code in states else "never",
                "latest_date": latest.isoformat() if latest else None,
                "first_date": first.isoformat() if first else None,
                "count": count,
                "unit": "条",
            })
        states_for_group = [
            entity["state"] for entity in commodity_entities if entity["enabled"]
        ]
        priority = {"fresh": 0, "stale": 1, "lagging": 2, "no_data": 3, "disabled": 4}
        non_index_groups.append({
            "label": "商品价格与分位",
            "source": "akshare",
            "job_id": "commodity_daily",
            "schedule": "交易日 15:50",
            "state": (
                max(states_for_group, key=lambda value: priority[value])
                if states_for_group
                else "disabled"
            ),
            "entities": commodity_entities,
        })

    # ---- 数据源 ----
    def _source_index_count(source_id):
        return sum(
            1
            for idx in load_universe()
            if any(ds.get("source") == source_id for ds in idx.get("datasets", {}).values())
        )

    sources = [
        {
            "id": sid,
            "name": meta["name"],
            "capabilities": [
                {"key": k, "label": v["label"]} for k, v in meta["capabilities"].items()
            ],
            "job_ids": meta["job_ids"],
            "index_count": _source_index_count(sid),
        }
        for sid, meta in SOURCES.items()
    ]

    return {
        "indexes": indexes,
        "non_index_groups": non_index_groups,
        "jobs": get_job_runs(db),
        "sources": sources,
    }
