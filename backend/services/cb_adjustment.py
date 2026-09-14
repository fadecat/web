# -*- coding: utf-8 -*-
"""可转债「转股价下修记录」按需代理(集思录 adj_logs 接口, 不落库)。

数据形态: GET /data/cbnew/adj_logs/?bond_id=xxx&adj_type=D 返回 HTML 片段,
tablesorter 表格每行 6 列: 转债名称/股东大会日/下修前转股价/下修后转股价/
新转股价生效日期/下修底价; 无记录时返回 '----'。

设计要点:
- 公开接口无需登录态(详情页同板块标「仅会员可见」只挡页面渲染, 不挡此 API),
  只带 UA/Referer 直拉, 不消耗集思录登录配额;
- 下修记录是低频追加的历史事实, 进程内缓存 TTL 7 天, 空列表同为有效终态照常缓存;
- 与 cb_discussion 同款 6 位代码校验 + 缓存结构, 行数应与 cell 的 adj_scnt 一致。
"""
from __future__ import annotations

import re
import threading
import time

import httpx

from backend.services.jisilu import LOGIN_HEADERS
from backend.utils import parse_float

_LOG_URL = "https://www.jisilu.cn/data/cbnew/adj_logs/"
_ADJ_TYPE_DOWNGRADE = "D"  # D=下修记录; U=不下修历史; A=全部

_CACHE_TTL_SECONDS = 7 * 24 * 3600
_FETCH_TIMEOUT = 10

_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()

# <tr><td>美锦转债</td><td>2026-07-16</td><td>5.260</td><td>3.470</td>
# <td>2026-07-17</td><td>3.470</td></tr> (thead 为 th, 天然不命中)
_ROW_RE = re.compile(r"<tr>((?:<td>[^<]*</td>)+)</tr>")


def _validate_bond_id(bond_id: str) -> str:
    """转债代码必须是 6 位数字(拼 URL 前先收口, 拒绝任意输入)。"""
    if not re.fullmatch(r"\d{6}", bond_id or ""):
        raise ValueError("转债代码须为 6 位数字")
    return bond_id


def _parse_logs(html: str) -> list[dict]:
    """从 adj_logs HTML 片段提取下修记录; 空表/'----' 返回空列表。

    列序: 转债名称/股东大会日/下修前转股价/下修后转股价/新转股价生效日期/下修底价,
    名称列冗余(即本债)不输出; 列不足时截断容忍, 价格解析失败为 None。
    """
    items = []
    for row in _ROW_RE.finditer(html):
        cells = re.findall(r"<td>([^<]*)</td>", row.group(1))
        if len(cells) < 5:
            continue
        items.append({
            "meeting_date": cells[1].strip(),
            "price_before": parse_float(cells[2].strip()),
            "price_after": parse_float(cells[3].strip()),
            "effective_date": cells[4].strip(),
            "floor_price": parse_float(cells[5].strip()) if len(cells) > 5 else None,
        })
    return items


def get_adjustment_logs(bond_id: str) -> list[dict]:
    """单只转债的下修记录列表(带缓存); bond_id 非 6 位数字抛 ValueError。"""
    bond_id = _validate_bond_id(bond_id)
    with _cache_lock:
        hit = _cache.get(bond_id)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]
    resp = httpx.get(
        _LOG_URL,
        params={"bond_id": bond_id, "adj_type": _ADJ_TYPE_DOWNGRADE},
        headers={
            "User-Agent": LOGIN_HEADERS["User-Agent"],
            "Referer": "https://www.jisilu.cn/data/convert_bond_detail/",
            "Accept": "text/html, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=_FETCH_TIMEOUT,
    )
    resp.raise_for_status()
    items = _parse_logs(resp.text)
    with _cache_lock:
        _cache[bond_id] = (time.monotonic(), items)
    return items
