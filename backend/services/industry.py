# -*- coding: utf-8 -*-
"""申万 2021 行业代码查表服务(转债细分行业列)。

数据: backend/data/sw_industry_2021.json(申万 2021 版, 源自东财掘金公开文档,
31 一级 + 134 二级 + 346 三级, by_code 共 511 条)。静态映射, 不触网。

口径: 集思录 cb_list_new 的 sw_cd 字段即申万 2021 三级代码(已落库于
CbDailySnapshot.sw_cd, 实时拉取的 cell 也带该字段), 与本表一致。

命名口径(B4 定版):「细分行业」= 该 sw_cd 自身层级的名称。
- 三级码(如 760201) -> 三级名「环保设备Ⅲ」
- 若码本身是二级/一级形态(如 610100) -> 该级名称「水泥」
- 不做一级回退冒充三级; 未知/缺失返回 None, 前端显示 —。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

_DEFAULT_MAP_PATH = Path(__file__).resolve().parents[1] / "data" / "sw_industry_2021.json"


@lru_cache(maxsize=1)
def _load_by_code() -> dict[str, dict]:
    """加载 by_code 索引 {code: {name, level, parent...}}, 进程内缓存一次。"""
    with open(_DEFAULT_MAP_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("by_code", {})


def industry_name_of(sw_cd: Optional[str]) -> Optional[str]:
    """sw_cd -> 该代码自身层级的行业名(不做一级回退)。

    - 正常三级码 -> 三级名(细分行业)
    - 二级/一级形态的码 -> 该级名称
    - 三级码在映射中缺失时, 回退查其二级码(code[:4]+"00"), 用于补像 610101
      (冀东转债)这类三级码未收录但二级码(610100 水泥)已收录的情况
    - 未知代码 / None / 空串 -> None(前端显示 —)
    """
    if not sw_cd:
        return None
    code = str(sw_cd).strip()
    if not code:
        return None
    entry = _load_by_code().get(code)
    if not entry:
        # B4 兜底: 三级码缺失时回退查二级码(len==6 且不以 "00" 结尾)
        if len(code) == 6 and not code.endswith("00"):
            entry = _load_by_code().get(code[:4] + "00")
        if not entry:
            return None
    name = entry.get("name")
    return str(name).strip() if name else None
