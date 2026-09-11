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


def industry_info_of(sw_cd: Optional[str]) -> Optional[dict]:
    """sw_cd -> 结构化行业信息 dict(方案 §3.3); 未知/缺失返回 None。

    返回字段:
    - industry_code: 原始码(规范化字符串, 筛选永远匹配原始码)
    - industry_name: 实际命中层级的名称(与 industry_name_of 同口径)
    - industry_level: 实际命中的映射层级(1/2/3)
    - industry_mapped_code: 实际命中的映射码(回退时为二级码, 否则等于原始码)
    - industry_is_fallback: 三级码缺失回退二级码时 True——降级映射必须
      可辨识, 不得把二级名称无标识地当作精确三级。

    未知原始码/空码返回 None(前端显示 —; 空码由调用方归一为枚举 NONE)。
    """
    if not sw_cd:
        return None
    code = str(sw_cd).strip()
    if not code:
        return None
    by_code = _load_by_code()
    entry = by_code.get(code)
    mapped_code = code
    is_fallback = False
    if entry is None:
        # B4 兜底: 三级码缺失时回退查二级码(len==6 且不以 "00" 结尾),
        # 用于补像 610101(冀东转债)这类三级码未收录但二级码(610100 水泥)
        # 已收录的情况; 回退必须显式标注
        if len(code) == 6 and not code.endswith("00"):
            fallback_code = code[:4] + "00"
            fallback_entry = by_code.get(fallback_code)
            if fallback_entry is not None:
                entry = fallback_entry
                mapped_code = fallback_code
                is_fallback = True
        if entry is None:
            return None
    name = entry.get("name")
    if not name:
        return None
    level = entry.get("level")
    return {
        "industry_code": code,
        "industry_name": str(name).strip(),
        "industry_level": int(level) if level is not None else None,
        "industry_mapped_code": mapped_code,
        "industry_is_fallback": is_fallback,
    }


def industry_name_of(sw_cd: Optional[str]) -> Optional[str]:
    """sw_cd -> 该代码自身层级的行业名(不做一级回退)。

    - 正常三级码 -> 三级名(细分行业)
    - 二级/一级形态的码 -> 该级名称
    - 三级码在映射中缺失时, 回退查其二级码(code[:4]+"00"), 用于补像 610101
      (冀东转债)这类三级码未收录但二级码(610100 水泥)已收录的情况
    - 未知代码 / None / 空串 -> None(前端显示 —)

    旧行为保持不变: 委托 industry_info_of 取名称(查表口径单一事实源)。
    """
    info = industry_info_of(sw_cd)
    return info["industry_name"] if info else None


# ---------------------------------------------------------------------------
# 行业目录(方案 §3.3/§6.2): 静态映射 + 本地快照发现, 不触网
# ---------------------------------------------------------------------------

def static_catalog_entries() -> list[dict]:
    """静态申万映射目录: 每个收录原始码一条目录项(§3.3)。

    条目结构(与 industry_info_of 对齐, 另加 source 标记来源):
    {industry_code, industry_name, industry_level, industry_mapped_code,
     industry_is_fallback, source}
    静态目录条目 source="catalog", 无回退(精确收录)。
    """
    by_code = _load_by_code()
    out: list[dict] = []
    for code in sorted(by_code):
        entry = by_code[code]
        name = entry.get("name")
        if not name:
            continue
        level = entry.get("level")
        out.append({
            "industry_code": code,
            "industry_name": str(name).strip(),
            "industry_level": int(level) if level is not None else None,
            "industry_mapped_code": code,
            "industry_is_fallback": False,
            "source": "catalog",
        })
    return out


def discovered_catalog_entry(sw_cd: str) -> dict:
    """快照发现的未收录原始码 → 目录项(不触网)。

    - 回退命中(如 610101 → 610100 水泥): 给出名称并 industry_is_fallback=True,
      前端选项文字须附回退层级, 防止多个原始码回退同名时误选;
    - 完全未知: name/level 为 None("未映射"由前端渲染), 目录仍保留该原始码,
      筛选匹配原始码(§3.3)。
    """
    code = str(sw_cd or "").strip()
    info = industry_info_of(code)
    if info is not None:
        return {
            "industry_code": code,
            "industry_name": info["industry_name"],
            "industry_level": info["industry_level"],
            "industry_mapped_code": info["industry_mapped_code"],
            "industry_is_fallback": bool(info["industry_is_fallback"]),
            "source": "snapshot",
        }
    return {
        "industry_code": code,
        "industry_name": None,
        "industry_level": None,
        "industry_mapped_code": code,
        "industry_is_fallback": False,
        "source": "snapshot",
    }
