# -*- coding: utf-8 -*-
"""评级目录(唯一事实源, P2-R04)。

语义冻结(P2-R07):
- NONE 只表示"缺失评级"占位符, 不是真实信用等级(is_missing=True)
- 空评级列表 = 不限(快速筛选默认), 不会因新增评级被旧"全选列表"排除
- 目录可发现未知上游评级: 数据库快照中出现未登记的非空值时,
  以 {value, label, order=None(未知), is_missing=False} 附加在末尾
"""
from __future__ import annotations

from typing import Any

# 规范等级目录: value=匹配用大写值, label=显示名, order=排序权重
# 未知上游值(如 BB+/B- 等细分)不需改此表, 会以"未知"条目出现在目录末尾
RATING_CATALOG: list[dict[str, Any]] = [
    {"value": "AAA", "label": "AAA", "order": 1, "is_missing": False},
    {"value": "AA+", "label": "AA+", "order": 2, "is_missing": False},
    {"value": "AA", "label": "AA", "order": 3, "is_missing": False},
    {"value": "AA-", "label": "AA-", "order": 4, "is_missing": False},
    {"value": "A+", "label": "A+", "order": 5, "is_missing": False},
    {"value": "A", "label": "A", "order": 6, "is_missing": False},
    {"value": "A-", "label": "A-", "order": 7, "is_missing": False},
    {"value": "BBB", "label": "BBB", "order": 8, "is_missing": False},
    {"value": "BB", "label": "BB", "order": 9, "is_missing": False},
    {"value": "B", "label": "B", "order": 10, "is_missing": False},
    {"value": "CCC", "label": "CCC", "order": 11, "is_missing": False},
    {"value": "CC", "label": "CC", "order": 12, "is_missing": False},
    {"value": "C", "label": "C", "order": 13, "is_missing": False},
    {"value": "NONE", "label": "无评级", "order": 14, "is_missing": True},
]

_KNOWN_VALUES = {entry["value"] for entry in RATING_CATALOG}


def get_rating_catalog(discovered_values: list[str] | None = None) -> list[dict[str, Any]]:
    """返回完整评级目录; discovered_values 为数据库快照中发现的非空评级,
    未登记的以未知条目附加在末尾(order=None)。"""
    catalog = [dict(e) for e in RATING_CATALOG]
    known_extra: list[str] = []
    seen = set(_KNOWN_VALUES)
    for raw in discovered_values or []:
        token = str(raw).strip().upper()
        if not token or token in seen:
            continue
        seen.add(token)
        known_extra.append(token)
    for token in sorted(known_extra):
        catalog.append({"value": token, "label": token, "order": None, "is_missing": False})
    return catalog
