# -*- coding: utf-8 -*-
"""FastAPI 应用工厂入口。

启动方式:
    uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import cb_index, cb_list, health, style_rotation, valuation
from backend.config import settings
from backend.models.database import init_db
from backend.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期: 初始化 DB,并按需启动调度。"""
    init_db()
    if settings.scheduler_enabled:
        start_scheduler()
    yield
    if settings.scheduler_enabled:
        stop_scheduler()


def create_app() -> FastAPI:
    """构造 FastAPI 应用。"""
    app = FastAPI(
        title="web 控制台 API",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    # CORS —— 前端 Vite 开发服务器(后续接入)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    prefix = "/api"
    app.include_router(health.router, prefix=prefix, tags=["health"])
    app.include_router(valuation.router, prefix=prefix, tags=["valuation"])
    app.include_router(style_rotation.router, prefix=prefix, tags=["style-rotation"])
    app.include_router(cb_index.router, prefix=prefix, tags=["cb-index"])
    app.include_router(cb_list.router, prefix=prefix, tags=["cb-list"])

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def api_not_found(path: str):
        raise HTTPException(status_code=404, detail="Not Found")

    return app


app = create_app()
