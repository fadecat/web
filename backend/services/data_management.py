# -*- coding: utf-8 -*-
"""数据管理页聚合服务: 按指数汇总各数据集的来源/最新日期/状态。

与 data_status.get_dataset_freshness 的区别:
  - 这里按「指数」聚合(一个指数一张卡片), 而非按「指标」分组;
  - 状态判定沿用 data_status 的 freshness 口径(fresh/stale/lagging/no_data),
    但绝不把「更新较晚」伪装成「抓取失败」——失败只来自任务运行记录。
"""
from __future__ import annotations

from datetime import date

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
from backend.services.data_status import (
    _expected_date,
    _freshness_state,
    get_job_runs,
)
from backend.services.index_universe import SOURCES, load_universe


def _dataset_state(latest: date | None, expected: date) -> str:
    return _freshness_state(latest, expected)


def build_data_management(db: Session) -> dict:
    """数据管理页完整数据: 指数列表(按指数聚合) + 非指数分组 + 任务 + 数据源。"""
    expected = _expected_date()

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
