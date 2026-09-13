# -*- coding: utf-8 -*-
"""可转债「相关讨论」路由(集思录详情页按需代理, 不落库)。

端点:
1. GET  /api/cb-discussion/{bond_id} — 单只转债的相关讨论列表(悬浮惰性)
2. POST /api/cb-discussion/batch      — 批量预热(前端筛选结果当前页)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.services import cb_discussion

router = APIRouter()


@router.get("/cb-discussion/{bond_id}")
def get_discussion(bond_id: str) -> dict[str, Any]:
    """单只转债的相关讨论帖子列表(标题/链接/回复/浏览/日期)。"""
    try:
        items = cb_discussion.get_discussions(bond_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 上游抓取失败
        raise HTTPException(status_code=502, detail=f"集思录抓取失败: {e}") from e
    return {"bond_id": bond_id, "items": items}


class BatchRequest(BaseModel):
    bond_ids: list[str] = Field(..., min_length=1, max_length=100, description="转债代码列表(6 位数字)")


@router.post("/cb-discussion/batch")
def discussion_batch(req: BatchRequest) -> dict[str, Any]:
    """批量预热相关讨论缓存; 单只失败跳过, 只返回成功部分。"""
    try:
        items = cb_discussion.get_discussions_batch(req.bond_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"集思录批量抓取失败: {e}") from e
    return {"items": items}
