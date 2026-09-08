# -*- coding: utf-8 -*-
"""数据管理 API 测试: 按指数聚合 / 探测失败如实上报 / 添加校验。"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

from backend.api.routes import data_management as dm
from backend.services.data_management import build_data_management
from backend.services import index_universe as iu
from backend.services.index_universe import index_by_code


def _seed_snapshot(db, code, pe=None, pb=None):
    from backend.models.valuation import IndexValuationSnapshot

    db.add(IndexValuationSnapshot(
        index_code=code, index_name=code, trade_date=date(2026, 9, 7),
        pe=pe, pb=pb,
    ))
    db.commit()


def test_build_data_management_aggregates_by_index(db):
    """930955 有 PE/PB 数据时, 数据管理视图按指数聚合出 pe/pb 列。"""
    _seed_snapshot(db, "930955", pe=15.0, pb=1.5)

    result = build_data_management(db)

    assert {"indexes", "non_index_groups", "jobs", "sources"} <= set(result.keys())

    idx = next(i for i in result["indexes"] if i["code"] == "930955")
    keys = {d["key"] for d in idx["datasets"]}
    # valuation 拆成 pe/pb/dividend 三列; quote 来自 eod
    assert {"pe", "pb", "dividend", "quote"} <= keys

    pe_col = next(d for d in idx["datasets"] if d["key"] == "pe")
    assert pe_col["latest_date"] == "2026-09-07"
    assert pe_col["source"] == "efunds"

    # 非指数分组含国债/转债
    assert any(g["label"].startswith("国债") for g in result["non_index_groups"])


def test_build_data_management_sources_have_counts(db):
    result = build_data_management(db)
    by_id = {s["id"]: s for s in result["sources"]}
    assert by_id["efunds"]["index_count"] > 0
    assert by_id["tencent"]["index_count"] == 2


def test_probe_efunds_reports_error_not_unavailable(monkeypatch):
    """网络失败必须标 error(检查失败), 不能伪造为『不支持』。"""
    from backend.services.fetchers import valuation as val_mod
    from backend.services.fetchers import index_eod as eod_mod

    def _boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(val_mod, "fetch_index_detail", _boom)
    monkeypatch.setattr(eod_mod, "fetch_index_eod_price", _boom)

    result = dm.probe_index({"code": "930955", "source": "efunds"})

    assert result["probe_token"]
    statuses = {c["key"]: c["status"] for c in result["capabilities"]}
    assert statuses["valuation"] == "error"
    assert statuses["quote"] == "error"


def test_probe_rejects_bad_code():
    with pytest.raises(HTTPException) as exc:
        dm.probe_index({"code": "abc", "source": "efunds"})
    assert exc.value.status_code == 400


def test_add_index_requires_probe_token(monkeypatch, tmp_universe):
    f = tmp_universe
    monkeypatch.setattr(iu, "_UNIVERSE_FILE", f)
    with pytest.raises(HTTPException) as exc:
        dm.add_index({
            "code": "000001", "source": "efunds",
            "name": "上证", "datasets": ["quote"], "probe_token": "bad",
        })
    assert exc.value.status_code == 400


def test_add_index_requires_available_capability(monkeypatch, tmp_universe):
    """勾选未通过探测的能力必须被拒绝, 不能盲目落名单。"""
    f = tmp_universe
    monkeypatch.setattr(iu, "_UNIVERSE_FILE", f)
    monkeypatch.setattr(dm, "_trigger_sync", lambda *a, **k: ("started", "ok"))

    # 构造一个只有 valuation 可用、quote 不可用的探测 token
    token = "tok123"
    dm._probes[token] = {
        "code": "000001",
        "source": "efunds",
        "name": "上证",
        "expires": 9999999999,
        "capabilities": [
            {"key": "valuation", "label": "PE / PB / 股息率", "status": "available", "message": ""},
            {"key": "quote", "label": "收盘价", "status": "unavailable", "message": "无数据"},
        ],
    }

    with pytest.raises(HTTPException) as exc:
        dm.add_index({
            "code": "000001", "source": "efunds",
            "name": "上证", "datasets": ["quote"], "probe_token": token,
        })
    assert exc.value.status_code == 400


def test_add_index_saves_and_syncs(monkeypatch, tmp_universe):
    """合法添加应写名单并触发同步。"""
    monkeypatch.setattr(iu, "_UNIVERSE_FILE", tmp_universe)
    monkeypatch.setattr(dm, "_trigger_sync", lambda *a, **k: ("started", "ok"))

    token = "tok456"
    dm._probes[token] = {
        "code": "000001",
        "source": "tencent",
        "name": "上证",
        "expires": 9999999999,
        "capabilities": [
            {"key": "quote", "label": "日K", "status": "available", "message": ""},
        ],
    }

    result = dm.add_index({
        "code": "000001", "source": "tencent",
        "name": "上证", "datasets": ["quote"], "probe_token": token,
    })

    assert result["status"] == "saved"
    assert result["sync_status"] == "started"
    # 已写入统一名单
    from backend.services.index_universe import index_by_code

    assert index_by_code("000001") is not None
    assert index_by_code("000001")["datasets"]["quote"]["source"] == "tencent"


def test_set_index_enabled_toggles_without_delete(monkeypatch, tmp_universe):
    f = tmp_universe
    monkeypatch.setattr(iu, "_UNIVERSE_FILE", f)
    # 用默认名单落盘
    iu.save_universe(iu.load_universe())

    result = dm.set_index_enabled("930955", {"enabled": False})
    assert result["enabled"] is False
    assert index_by_code("930955")["enabled"] is False
    # 数据仍在名单(未删除)
    assert index_by_code("930955")["datasets"]


def test_set_index_enabled_unknown_404():
    with pytest.raises(HTTPException) as exc:
        dm.set_index_enabled("999999", {"enabled": False})
    assert exc.value.status_code == 404
