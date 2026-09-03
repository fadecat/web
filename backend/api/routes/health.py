# -*- coding: utf-8 -*-
"""健康检查路由。"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """存活探针。"""
    return {"status": "ok"}
