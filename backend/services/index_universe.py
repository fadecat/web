# -*- coding: utf-8 -*-
"""统一指数名单(单一事实源)。

把散落三处的指数配置合并为一个运行时名单:
  1. config/valuation.yaml   —— 易方达估值(PE/PB/股息率)
  2. config/index_eod.yaml   —— 易方达日收盘价(close)
  3. style_rotation.py 常量   —— 腾讯日 K(399376/399373)

每个指数按 dataset 独立绑定来源与存储键:
  - quote     : 日线(易方达=收盘价 / 腾讯=日K), 入 IndexDailyQuote
  - valuation : PE/PB(+PS)+股息率, 入 IndexValuationSnapshot / IndexDividendYield

存储键(storage_code)与来源代码(symbol)解耦: 历史库按旧 config code 落库
(如 中证价值100 → 512040 ETF 代码), 抓取用真实指数代码(931052), 两者靠
storage_code / symbol 分开, 保证既有数据不重写、新抓取不查错表。

持久化: data/index_universe.json(运行时增删改的落点, 不进 git)。
        文件不存在时, 从上述三处合并生成"默认名单"内存返回(不写文件),
        行为与旧三份配置完全一致。
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any

from backend.config import DATA_DIR
from backend.utils import load_index_eod_targets, load_valuation_targets

# dataset 类型
DATASET_QUOTE = "quote"          # 日线
DATASET_VALUATION = "valuation"  # PE/PB(+PS)+股息率(同一抓取单元)

# 数据源
SOURCE_EFUNDS = "efunds"
SOURCE_TENCENT = "tencent"

# 数据源能力(数据源页展示 / 探测白名单)
SOURCES: dict[str, dict[str, Any]] = {
    SOURCE_EFUNDS: {
        "name": "易方达",
        "capabilities": {
            "quote": {"label": "收盘价"},
            "valuation": {"label": "PE / PB / 股息率"},
        },
        "job_ids": ["valuation_daily", "index_eod_daily"],
    },
    SOURCE_TENCENT: {
        "name": "腾讯",
        "capabilities": {
            "quote": {"label": "日K"},
        },
        "job_ids": ["style_rotation_daily"],
    },
}

_UNIVERSE_FILE = DATA_DIR / "index_universe.json"
_lock = threading.RLock()


def _canonical_from_detail_url(url: str | None) -> str | None:
    """从易方达 detail URL 提取真实指数代码 indexCode=XXX。"""
    if not url:
        return None
    m = re.search(r"indexCode=(\d+)", url)
    return m.group(1) if m else None


def _tencent_constants() -> list[tuple[str, str]]:
    """腾讯标的常量(避免循环 import, 延迟加载)。"""
    from backend.services.fetchers.style_rotation import (
        LEFT_NAME,
        LEFT_SYMBOL,
        RIGHT_NAME,
        RIGHT_SYMBOL,
    )

    return [(LEFT_SYMBOL, LEFT_NAME), (RIGHT_SYMBOL, RIGHT_NAME)]


def _merge_default_universe() -> dict[str, dict[str, Any]]:
    """从三处旧配置合并生成默认名单, 键 = canonical code。"""
    indices: dict[str, dict[str, Any]] = {}

    def _ensure(canonical: str, name: str) -> dict[str, Any]:
        idx = indices.setdefault(
            canonical,
            {"code": canonical, "name": name, "enabled": True, "datasets": {}},
        )
        # 名称取第一个非空来源(各配置应一致)
        if not idx["name"]:
            idx["name"] = name
        return idx

    # 1) 估值标的 → valuation dataset(symbol=真实指数, storage=config code)
    for t in load_valuation_targets():
        code = str(t.get("code", ""))
        if not code:
            continue
        canonical = _canonical_from_detail_url(t.get("index_detail_url", "")) or code
        idx = _ensure(canonical, t.get("name") or code)
        idx["datasets"][DATASET_VALUATION] = {
            "source": SOURCE_EFUNDS,
            "storage_code": code,
            "symbol": canonical,
            "enabled": True,
        }

    # 2) 易方达日收盘价 → quote dataset(efunds, code 即真实代码)
    for t in load_index_eod_targets():
        code = str(t.get("code", ""))
        if not code:
            continue
        idx = _ensure(code, t.get("name") or code)
        if DATASET_QUOTE not in idx["datasets"]:
            idx["datasets"][DATASET_QUOTE] = {
                "source": SOURCE_EFUNDS,
                "storage_code": code,
                "symbol": code,
                "enabled": True,
            }

    # 3) 腾讯日 K → quote dataset(tencent)
    for code, name in _tencent_constants():
        idx = _ensure(code, name)
        if DATASET_QUOTE not in idx["datasets"]:
            idx["datasets"][DATASET_QUOTE] = {
                "source": SOURCE_TENCENT,
                "storage_code": code,
                "symbol": code,
                "enabled": True,
            }

    return indices


def _read_universe_file() -> list[dict[str, Any]] | None:
    """读运行时名单 JSON; 不存在返回 None。"""
    if not _UNIVERSE_FILE.exists():
        return None
    try:
        with open(_UNIVERSE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        indices = data.get("indices")
        if isinstance(indices, list):
            return indices
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return None


def load_universe() -> list[dict[str, Any]]:
    """返回统一名单(运行时 JSON 优先, 否则默认名单), 已按 code 排序。"""
    with _lock:
        indices = _read_universe_file()
        if indices is None:
            default = _merge_default_universe()
            indices = list(default.values())
        return sorted(indices, key=lambda x: x.get("code", ""))


def save_universe(indices: list[dict[str, Any]]) -> None:
    """原子写回运行时名单 JSON。"""
    payload = {"version": 1, "indices": indices}
    tmp = _UNIVERSE_FILE.with_suffix(".json.tmp")
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _UNIVERSE_FILE)


def index_by_code(code: str) -> dict[str, Any] | None:
    """按 canonical code 查单个指数。"""
    for idx in load_universe():
        if idx.get("code") == code:
            return idx
    return None


def resolve_storage(code: str, dataset: str) -> str:
    """canonical code → 指定 dataset 的历史存储键; 未知/未绑定恒等返回。"""
    idx = index_by_code(code)
    if idx:
        ds = idx.get("datasets", {}).get(dataset)
        if ds and ds.get("storage_code"):
            return ds["storage_code"]
    return code


def datasets_for_job(job_id: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """按任务返回需要处理的 (index, dataset) 列表。

    估值任务(valuation_daily): 处理所有 enabled 的 valuation dataset。
    易方达 eod(index_eod_daily): 处理 enabled 的 quote dataset(source=efunds)。
    腾讯日线(style_rotation_daily): 处理 enabled 的 quote dataset(source=tencent)。
    """
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for idx in load_universe():
        if idx.get("enabled") is False:
            continue
        datasets = idx.get("datasets", {})
        if job_id == "valuation_daily":
            ds = datasets.get(DATASET_VALUATION)
            if ds and ds.get("enabled", True):
                out.append((idx, ds))
        elif job_id == "index_eod_daily":
            ds = datasets.get(DATASET_QUOTE)
            if ds and ds.get("source") == SOURCE_EFUNDS and ds.get("enabled", True):
                out.append((idx, ds))
        elif job_id == "style_rotation_daily":
            ds = datasets.get(DATASET_QUOTE)
            if ds and ds.get("source") == SOURCE_TENCENT and ds.get("enabled", True):
                out.append((idx, ds))
    return out
