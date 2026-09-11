# -*- coding: utf-8 -*-
"""实时选债 gateway: 实时拉集思录, 不落库。

与历史快照查询(收盘语义)严格分离 —— 实时数据纯内存流转,
快照表「收盘后定时任务」语义不被污染。纯过滤留 cb_intraday。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from dataclasses import dataclass

from loguru import logger

from backend.services.fetchers.cb_list import fetch_cb_list
from backend.services.fetchers.cb_redeem import fetch_redeem_list


@dataclass
class LiveSnapshot:
    records: list[dict[str, Any]]
    redeem_cells: list[dict[str, Any]]
    redeem_fetch_status: str

    def __iter__(self):
        # 保持旧调用方的二元解包兼容。
        yield self.records
        yield self.redeem_cells


def fetch_live_snapshot() -> LiveSnapshot:
    """并行拉取实时转债列表与强赎列表, 返回 (records, redeem_cells)。

    强赎列表失败不阻塞选债(降级为空, 仅赎回价/保本价差/到期收益率/强赎状态缺数据),
    转债列表失败直接抛(主数据, 没得筛)。
    """
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_list = ex.submit(fetch_cb_list)
        f_redeem = ex.submit(fetch_redeem_list)

        records = f_list.result()  # 失败会抛, 由上层转 HTTPException

        try:
            redeem_cells = f_redeem.result()
            status = "ok" if redeem_cells else "empty"
        except Exception as exc:
            logger.warning(f"盘中选债: 强赎列表拉取失败(降级为无强赎数据): {exc}")
            redeem_cells = []
            status = "failed"

    return LiveSnapshot(records, redeem_cells, status)
