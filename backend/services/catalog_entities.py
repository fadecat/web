# -*- coding: utf-8 -*-
"""实体身份映射: canonical_id / legacy_storage_code / source_symbol。

背景(架构审查讨论稿 P2): valuation.yaml 里部分标的的 config code 是 ETF 代码,
而真实指数代码在 index_detail_url 的 indexCode 参数里, 二者混用导致「同一指数
两个标识」。例如 中证价值100 → config code=512040(ETF 份额), 真实指数=931052。
历史库已按 config code(512040) 落库, 不可再生资产不能重写。

本层只建映射, 不迁移历史主键(讨论稿「先映射后迁移」):
- canonical_id:        真实指数代码, 唯一、稳定、跨源通用的身份
- legacy_storage_code: 当前数据库实际存储的 key(即 config code)
- source_symbol:       各数据源使用的代码。本项目中恒等于 canonical_id,
                      因为易方达 indexCode 与腾讯代码本就是真实指数代码。

映射信息只从现有两份 YAML + 腾讯常量派生, 不新增独立配置文件, 不写库。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from backend.utils import load_index_eod_targets, load_valuation_targets


@dataclass(frozen=True)
class Entity:
    """一个指数实体的身份。"""

    canonical_id: str
    name: str
    legacy_storage_code: str
    providers: frozenset[str]  # 提供该实体数据的数据源(efunds/tencent/...)


def _extract_index_code(url: str) -> str | None:
    """从易方达 detail URL 提取真实指数代码 indexCode=XXX。"""
    if not url:
        return None
    m = re.search(r"indexCode=(\d+)", url)
    return m.group(1) if m else None


@lru_cache(maxsize=1)
def build_entity_catalog() -> dict[str, Entity]:
    """生成实体目录, 以 canonical_id 为键。

    来源与口径:
    - 估值标的:   canonical = indexCode(真实指数), legacy = config code
    - eod 日线标的: canonical = legacy = code(已一致)
    - 腾讯日线标的: canonical = legacy = code(已一致)
    """
    entities: dict[str, Entity] = {}

    for t in load_valuation_targets():
        code = str(t.get("code", ""))
        if not code:
            continue
        name = t.get("name") or code
        canonical = _extract_index_code(t.get("index_detail_url", "")) or code
        entities.setdefault(
            canonical,
            Entity(
                canonical_id=canonical,
                name=name,
                legacy_storage_code=code,
                providers=frozenset({"efunds"}),
            ),
        )

    for t in load_index_eod_targets():
        code = str(t.get("code", ""))
        if not code:
            continue
        name = t.get("name") or code
        entities.setdefault(
            code,
            Entity(
                canonical_id=code,
                name=name,
                legacy_storage_code=code,
                providers=frozenset({"efunds"}),
            ),
        )

    from backend.services.fetchers.style_rotation import (
        LEFT_NAME,
        LEFT_SYMBOL,
        RIGHT_NAME,
        RIGHT_SYMBOL,
    )

    for code, name in [(LEFT_SYMBOL, LEFT_NAME), (RIGHT_SYMBOL, RIGHT_NAME)]:
        entities.setdefault(
            code,
            Entity(
                canonical_id=code,
                name=name,
                legacy_storage_code=code,
                providers=frozenset({"tencent"}),
            ),
        )

    return entities


def to_storage(canonical_id: str) -> str:
    """canonical_id -> 历史存储 key; 未知 code 恒等返回。"""
    e = build_entity_catalog().get(canonical_id)
    return e.legacy_storage_code if e else canonical_id


def to_canonical(storage_code: str) -> str:
    """历史存储 key -> canonical_id; 未知 code 恒等返回。"""
    for e in build_entity_catalog().values():
        if e.legacy_storage_code == storage_code:
            return e.canonical_id
    return storage_code


def is_aliased(canonical_id: str) -> bool:
    """canonical_id 与历史存储 key 不一致(即错关联实体)。"""
    return to_storage(canonical_id) != canonical_id


def aliases() -> dict[str, str]:
    """列出所有 canonical_id -> legacy_storage_code 有差异的映射。"""
    return {
        e.canonical_id: e.legacy_storage_code
        for e in build_entity_catalog().values()
        if e.canonical_id != e.legacy_storage_code
    }
