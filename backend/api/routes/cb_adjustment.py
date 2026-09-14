# -*- coding: utf-8 -*-
"""可转债「转股价下修记录」路由(集思录 adj_logs 按需代理, 不落库)。

端点: GET /api/cb-adjustment/{bond_id} — 单只转债的下修记录(点击转股价弹窗惰性拉取)。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.services import cb_adjustment

router = APIRouter()


@router.get("/cb-adjustment/{bond_id}")
def get_adjustment(bond_id: str) -> dict[str, Any]:
    """单只转债的下修记录列表(股东大会日/下修前后转股价/生效日/下修底价)。"""
    try:
        items = cb_adjustment.get_adjustment_logs(bond_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 上游抓取失败
        raise HTTPException(status_code=502, detail=f"集思录抓取失败: {e}") from e
    return {"bond_id": bond_id, "items": items}
