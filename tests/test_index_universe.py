# -*- coding: utf-8 -*-
"""统一指数名单服务测试: 默认合并 / 存储键分离 / 任务映射 / 持久化。"""
from __future__ import annotations

import json

from backend.services import index_universe as iu
from backend.services.index_universe import (
    DATASET_QUOTE,
    DATASET_VALUATION,
    SOURCE_EFUNDS,
    SOURCE_TENCENT,
    datasets_for_job,
    index_by_code,
    load_universe,
    resolve_storage,
    save_universe,
)


def test_default_universe_merges_three_sources():
    """默认名单应合并估值(9) + 腾讯(2) = 11 个指数, 且 931052 存储键=512040。"""
    indices = {i["code"]: i for i in load_universe()}

    # 估值标的 canonical 是真实指数代码
    assert "931052" in indices, "中证价值100 canonical 应为真实指数 931052"
    assert indices["931052"]["datasets"][DATASET_VALUATION]["storage_code"] == "512040"
    assert indices["931052"]["datasets"][DATASET_VALUATION]["symbol"] == "931052"

    # 980081(国证价值100) storage=159263
    assert indices["980081"]["datasets"][DATASET_VALUATION]["storage_code"] == "159263"

    # 腾讯两只 quote 独立
    assert indices["399376"]["datasets"][DATASET_QUOTE]["source"] == SOURCE_TENCENT
    assert indices["399373"]["datasets"][DATASET_QUOTE]["source"] == SOURCE_TENCENT

    # 易方达 eod 两只(同时是估值标的) quote source=efunds
    assert indices["930955"]["datasets"][DATASET_QUOTE]["source"] == SOURCE_EFUNDS
    assert indices["399296"]["datasets"][DATASET_QUOTE]["source"] == SOURCE_EFUNDS


def test_datasets_for_job_routing():
    """三个任务各取正确的 dataset 集合。"""
    val_codes = {i["code"] for i, _d in datasets_for_job("valuation_daily")}
    assert "931052" in val_codes and "930955" in val_codes
    assert "399376" not in val_codes  # 腾讯不是估值任务

    eod_codes = {i["code"] for i, _d in datasets_for_job("index_eod_daily")}
    assert eod_codes == {"930955", "399296"}

    tencent_codes = {i["code"] for i, _d in datasets_for_job("style_rotation_daily")}
    assert tencent_codes == {"399376", "399373"}


def test_resolve_storage_by_dataset():
    assert resolve_storage("931052", DATASET_VALUATION) == "512040"
    assert resolve_storage("930955", DATASET_QUOTE) == "930955"
    assert resolve_storage("999999", DATASET_QUOTE) == "999999"  # 未知恒等


def test_save_and_load_roundtrip(tmp_universe):
    """运行时名单 JSON 落盘后可读回, 且不依赖旧 YAML。"""
    f = tmp_universe
    indices = [
        {
            "code": "000001",
            "name": "上证指数",
            "enabled": True,
            "datasets": {
                DATASET_QUOTE: {
                    "source": SOURCE_TENCENT,
                    "storage_code": "000001",
                    "symbol": "000001",
                    "enabled": True,
                }
            },
        }
    ]
    save_universe(indices)

    loaded = load_universe()
    assert len(loaded) == 1
    assert loaded[0]["code"] == "000001"
    # 落盘内容含 version 包裹
    raw = json.loads(f.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["indices"][0]["code"] == "000001"


def test_index_by_code():
    assert index_by_code("930955") is not None
    assert index_by_code("nope") is None
