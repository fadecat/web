# -*- coding: utf-8 -*-
"""数据管理路由: 按指数聚合查看 + 添加/探测/同步/启停指数。

写操作(探测/添加/同步)涉及真实数据源请求与运行时名单落盘, 均无鉴权
(与现有 data-status 手动触发一致), 公网鉴权属独立待办, 不在本模块处理。
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.data_status import TaskRunLog
from backend.models.database import get_db
from backend.services.data_management import build_data_management
from backend.services.index_universe import (
    DATASET_QUOTE,
    DATASET_VALUATION,
    SOURCE_EFUNDS,
    SOURCE_TENCENT,
    SOURCES,
    index_by_code,
    load_universe,
    save_universe,
)
from backend.services.run_logger import reserve_job, run_with_logging
from backend.tasks.registry import JOB_FUNCS

router = APIRouter(prefix="/data-management")

_CODE_RE = re.compile(r"^\d{6}$")

# 探测结果短期缓存: token -> {code, source, capabilities, name, expires}
_probes: dict[str, dict[str, Any]] = {}
_probes_lock = threading.Lock()
_PROBE_TTL_SEC = 600


def _valid_source(source: str) -> str:
    if source not in SOURCES:
        raise HTTPException(status_code=400, detail=f"未知数据源: {source}")
    return source


def _overview(rows: list[dict[str, Any]], date_key: str) -> dict[str, Any]:
    """从已抓取的记录列表提炼概览: 首日期/最新日期/条数。"""
    dates = [str(r.get(date_key, "")) for r in rows if r.get(date_key)]
    dates = [d for d in dates if d]
    if not dates:
        return {}
    return {
        "first_date": min(dates),
        "latest_date": max(dates),
        "count": len(rows),
    }


def _clean_probes() -> None:
    now = time.time()
    with _probes_lock:
        for token in [t for t, v in _probes.items() if v["expires"] < now]:
            _probes.pop(token, None)


@router.get("")
def get_data_management(db: Session = Depends(get_db)) -> dict:
    return build_data_management(db)


@router.get("/runs")
def get_runs(limit: int = 50, db: Session = Depends(get_db)) -> dict:
    limit = max(1, min(limit, 200))
    rows = db.execute(
        select(TaskRunLog).order_by(TaskRunLog.started_at.desc()).limit(limit)
    ).scalars().all()
    return {
        "runs": [
            {
                "job_id": r.job_id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "duration_sec": r.duration_sec,
                "status": r.status,
                "summary": r.summary,
                "error": r.error,
            }
            for r in rows
        ]
    }


@router.post("/probe")
def probe_index(payload: dict[str, Any]) -> dict:
    code = str(payload.get("code", "")).strip()
    source = _valid_source(str(payload.get("source", "")).strip())
    if not _CODE_RE.match(code):
        raise HTTPException(status_code=400, detail="指数代码应为 6 位数字")

    _clean_probes()
    capabilities: list[dict[str, Any]] = []
    name = ""

    if source == SOURCE_EFUNDS:
        # valuation: detail 接口确认名称 + 估值分位/股息率 URL
        try:
            from backend.services.fetchers.valuation import (
                fetch_index_detail,
                fetch_index_valuation_percentile,
            )

            detail = fetch_index_detail(code)
            name = detail.get("index_name", "")
            has_val = bool(detail.get("index_valuation_percentile_url"))
            cap = {
                "key": DATASET_VALUATION,
                "label": "PE / PB / 股息率",
                "status": "available" if has_val else "unavailable",
                "message": "" if has_val else "该指数暂无估值数据",
            }
            if has_val:
                # 概览: 探测时顺手拉一次估值全历史, 数条数取起止(保存时同一接口会再拉, 幂等)
                try:
                    rows = fetch_index_valuation_percentile(
                        code, url=detail.get("index_valuation_percentile_url", "")
                    )
                    if rows:
                        cap["overview"] = _overview(rows, "trade_date")
                except Exception:
                    pass  # 概览失败不影响"支持"判定
            capabilities.append(cap)
        except Exception as exc:
            capabilities.append({
                "key": DATASET_VALUATION,
                "label": "PE / PB / 股息率",
                "status": "error",
                "message": f"检查失败: {type(exc).__name__}",
            })
        # quote: eod 收盘价
        try:
            from backend.services.fetchers.index_eod import fetch_index_eod_price

            records = fetch_index_eod_price(code)
            cap = {
                "key": DATASET_QUOTE,
                "label": "收盘价",
                "status": "available" if records else "unavailable",
                "message": "" if records else "该指数暂无收盘价数据",
            }
            if records:
                cap["overview"] = _overview(records, "date")
            capabilities.append(cap)
        except Exception as exc:
            capabilities.append({
                "key": DATASET_QUOTE,
                "label": "收盘价",
                "status": "error",
                "message": f"检查失败: {type(exc).__name__}",
            })
    else:  # tencent
        try:
            from backend.services.fetchers.style_rotation import fetch_index_kline

            # 探测拉近一年 K 线做概览(完整回补时同样数据, 探测不至于太慢)
            records = fetch_index_kline(code, limit=320)
            cap = {
                "key": DATASET_QUOTE,
                "label": "日K",
                "status": "available" if records else "unavailable",
                "message": "" if records else "该指数暂无日K数据",
            }
            if records:
                cap["overview"] = _overview(records, "date")
            capabilities.append(cap)
        except Exception as exc:
            capabilities.append({
                "key": DATASET_QUOTE,
                "label": "日K",
                "status": "error",
                "message": f"检查失败: {type(exc).__name__}",
            })

    token = uuid.uuid4().hex
    with _probes_lock:
        _probes[token] = {
            "code": code,
            "source": source,
            "capabilities": capabilities,
            "name": name,
            "expires": time.time() + _PROBE_TTL_SEC,
        }
    return {
        "code": code,
        "name": name,
        "source": source,
        "capabilities": capabilities,
        "probe_token": token,
    }


@router.post("/indexes")
def add_index(payload: dict[str, Any]) -> dict:
    code = str(payload.get("code", "")).strip()
    source = _valid_source(str(payload.get("source", "")).strip())
    name = str(payload.get("name", "")).strip() or code
    datasets = payload.get("datasets") or []
    token = str(payload.get("probe_token", "")).strip()

    if not _CODE_RE.match(code):
        raise HTTPException(status_code=400, detail="指数代码应为 6 位数字")
    if not isinstance(datasets, list) or not datasets:
        raise HTTPException(status_code=400, detail="请选择至少一类数据")

    # 校验探测绑定
    _clean_probes()
    with _probes_lock:
        probe = _probes.get(token)
    if not probe or probe["code"] != code or probe["source"] != source:
        raise HTTPException(status_code=400, detail="探测已失效, 请先重新检查")

    # 勾选项必须是探测为 available 的能力
    avail = {c["key"]: c for c in probe["capabilities"] if c["status"] == "available"}
    for key in datasets:
        if key not in avail:
            raise HTTPException(
                status_code=400,
                detail=f"数据 {key} 检查未通过, 无法添加",
            )

    # 合并进统一名单: 已存在的指数不覆盖冲突来源/旧存储键
    indices = load_universe()
    existing = next((i for i in indices if i.get("code") == code), None)
    if existing is None:
        existing = {"code": code, "name": name, "enabled": True, "datasets": {}}
        indices.append(existing)
    else:
        # 已存在: 保留旧名称与既有 dataset 绑定, 仅补充新勾选
        pass

    for key in datasets:
        if key == DATASET_VALUATION:
            existing["datasets"][DATASET_VALUATION] = {
                "source": source,
                "storage_code": code,
                "symbol": code,
                "enabled": True,
            }
        elif key == DATASET_QUOTE:
            existing["datasets"][DATASET_QUOTE] = {
                "source": source,
                "storage_code": code,
                "symbol": code,
                "enabled": True,
            }
    if name and (not existing.get("name") or existing.get("name") == code):
        existing["name"] = name

    save_universe(indices)

    # 触发初始同步(后台, 幂等): 按 source+dataset 对应任务
    sync_status, message = _trigger_sync(source, datasets)
    return {"code": code, "status": "saved", "sync_status": sync_status, "message": message}


@router.post("/indexes/{code}/sync")
def sync_index(code: str) -> dict:
    idx = index_by_code(code)
    if idx is None:
        raise HTTPException(status_code=404, detail="指数不存在")
    datasets = idx.get("datasets", {})
    if not datasets:
        raise HTTPException(status_code=400, detail="该指数未绑定任何数据")

    # 按来源汇总需要同步的 dataset key
    need: dict[str, list[str]] = {}
    for key, ds in datasets.items():
        if ds.get("enabled", True):
            need.setdefault(ds.get("source", ""), []).append(key)

    if not need:
        raise HTTPException(status_code=400, detail="该指数所有数据均已停用")

    statuses: list[str] = []
    for source, keys in need.items():
        st, _msg = _trigger_sync(source, keys)
        statuses.append(st)
    sync_status = "started" if any(s == "started" for s in statuses) else ("busy" if statuses else "failed")
    return {"code": code, "sync_status": sync_status}


@router.patch("/indexes/{code}")
def set_index_enabled(code: str, payload: dict[str, Any]) -> dict:
    idx = index_by_code(code)
    if idx is None:
        raise HTTPException(status_code=404, detail="指数不存在")
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled 应为布尔值")

    indices = load_universe()
    for i in indices:
        if i.get("code") == code:
            i["enabled"] = enabled
            break
    save_universe(indices)
    return {"code": code, "enabled": enabled}


def _trigger_sync(source: str, datasets: list[str]) -> tuple[str, str]:
    """按 source+dataset 触发对应任务的手动运行(幂等, 后台线程)。"""
    job_ids: list[str] = []
    if DATASET_VALUATION in datasets and source == SOURCE_EFUNDS:
        job_ids.append("valuation_daily")
    if DATASET_QUOTE in datasets:
        if source == SOURCE_EFUNDS:
            job_ids.append("index_eod_daily")
        elif source == SOURCE_TENCENT:
            job_ids.append("style_rotation_daily")

    if not job_ids:
        return "failed", "该来源/数据组合无对应任务"

    started_any = False
    busy = False
    for job_id in job_ids:
        func = JOB_FUNCS.get(job_id)
        if func is None:
            continue
        if not reserve_job(job_id):
            busy = True
            continue

        def _worker(jid=job_id, fn=func):
            run_with_logging(jid, fn, reserved=True, trigger="manual")

        threading.Thread(target=_worker, name=f"manual-{job_id}", daemon=True).start()
        started_any = True

    if started_any:
        return "started", "已开始后台同步(幂等, 会同时检查名单内同类指数, 不重复写入)"
    if busy:
        return "busy", "相关任务正在运行, 请稍后重试"
    return "failed", "未能启动同步任务"
